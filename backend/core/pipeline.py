"""The lead pipeline: plan -> search -> crawl -> extract -> validate -> store, as one job."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import Engine, text

from backend.core import config, crawler, db, discovery, domains, events, exports, extractor, jobs, leads, llm, planner, validator
from backend.core.extractor import LeadDraft
from backend.core.jobs import JobCancelled, JobContext
from backend.core.models import AppSettings, EmailConfidence, Mode
from backend.core.planner import Plan

log = logging.getLogger("reachout.pipeline")

KNOWN_DOMAIN_DAYS = 14
MAX_RUNTIME_S = 25 * 60
WIDEN_UNTIL_S = 12 * 60  # do not start another round after this
MAX_ROUNDS = 4
MAX_LOG_LINES = 2000
EVENT_MIN_INTERVAL_S = 0.25


@dataclass
class RunOptions:
    recrawl: bool = False
    use_overpass: bool = False
    expand_lists: bool = True
    max_domains: int | None = None


def create_run(request_text: str, plan: Plan, options: RunOptions, engine: Engine | None = None) -> dict:
    """Insert the run record and queue its job (executed by the worker)."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        run_id = int(c.execute(
            text("INSERT INTO search_runs (request_text, plan_json, mode, state) VALUES (:r, :p, :m, 'queued')"),
            {"r": request_text.strip(), "p": plan.model_dump_json(), "m": plan.mode.value},
        ).lastrowid)
    job = jobs.create_job("discovery_run", {"run_id": run_id, "options": options.__dict__}, engine)
    with engine.begin() as c:
        c.execute(text("UPDATE search_runs SET job_id = :j WHERE id = :i"), {"j": job["id"], "i": run_id})
    return get_run(run_id, engine) or {}


def get_run(run_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        r = c.execute(text("SELECT * FROM search_runs WHERE id = :i"), {"i": run_id}).mappings().first()
    if r is None:
        return None
    d = dict(r)
    d["plan"] = json.loads(d.pop("plan_json") or "{}")
    d["stats"] = json.loads(d.pop("stats_json") or "{}")
    return d


def reconcile_runs(engine: Engine | None = None) -> int:
    """A run whose job has already ended (worker crash, cancelled while still queued) must not stay
    'running' or 'queued' forever: the UI would think a search is active. Copies the job's outcome."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(text(
            "UPDATE search_runs SET state = (SELECT state FROM jobs WHERE jobs.id = search_runs.job_id), "
            "error = COALESCE(error, (SELECT error FROM jobs WHERE jobs.id = search_runs.job_id)), "
            "finished_at = COALESCE(finished_at, datetime('now')), stage = 'done' "
            "WHERE state IN ('queued', 'running') AND job_id IN "
            "(SELECT id FROM jobs WHERE state IN ('failed', 'cancelled', 'succeeded'))"))
        return res.rowcount


def list_runs(limit: int = 30, engine: Engine | None = None) -> list[dict]:
    engine = engine or db.get_engine()
    reconcile_runs(engine)
    with engine.connect() as c:
        ids = [r[0] for r in c.execute(text("SELECT id FROM search_runs ORDER BY id DESC LIMIT :n"), {"n": limit})]
    return [r for r in (get_run(i, engine) for i in ids) if r]


def run_domains(run_id: int, engine: Engine | None = None) -> list[dict]:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            text("SELECT domain, url, state, pages, emails, leads, note FROM run_domains WHERE run_id = :i ORDER BY id"),
            {"i": run_id}).mappings()]


def run_log(run_id: int, after: int = 0, limit: int = 300, engine: Engine | None = None) -> list[dict]:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(
            text("SELECT id, message, created_at FROM run_log WHERE run_id = :i AND id > :a ORDER BY id DESC LIMIT :n"),
            {"i": run_id, "a": after, "n": limit}).mappings()]
    return rows[::-1]


class _Cancel:
    """Cheap cancellation check: re-reads the DB flag at most once a second."""

    def __init__(self, ctx: JobContext):
        self.ctx, self._t, self._v = ctx, 0.0, False

    def __call__(self) -> bool:
        if not self._v and time.monotonic() - self._t > 1.0:
            self._t, self._v = time.monotonic(), self.ctx.cancelled()
        return self._v


class _Run:
    """Mutable run state shared by worker threads: stats, run row, log and events."""

    def __init__(self, run_id: int, engine: Engine, ctx: JobContext):
        self.id, self.engine, self.ctx = run_id, engine, ctx
        self.stats: Counter[str] = Counter()
        self.conf: Counter[str] = Counter()
        self.stage = "searching"
        self._lock = threading.Lock()
        self._last_emit = 0.0
        self._log_lines = 0

    def log(self, message: str) -> None:
        with self._lock:
            self._log_lines += 1
            if self._log_lines > MAX_LOG_LINES:
                return
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO run_log (run_id, message) VALUES (:r, :m)"), {"r": self.id, "m": message[:300]})
        events.emit("run_log", {"run_id": self.id, "message": message[:300]}, self.engine)

    def bump(self, **kw: int) -> None:
        with self._lock:
            for k, v in kw.items():
                self.stats[k] += v

    def snapshot(self) -> dict:
        with self._lock:
            return {**self.stats, "by_confidence": dict(self.conf)}

    def push(self, force: bool = False, state: str = "running") -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < EVENT_MIN_INTERVAL_S:
            return
        self._last_emit = now
        snap = self.snapshot()
        with self.engine.begin() as c:
            c.execute(text("UPDATE search_runs SET stage=:g, state=:s, stats_json=:j, pages_fetched=:p, leads_found=:l WHERE id=:i"),
                      {"g": self.stage, "s": state, "j": json.dumps(snap), "p": snap.get("pages", 0),
                       "l": snap.get("leads_new", 0), "i": self.id})
        events.emit("run", {"run_id": self.id, "state": state, "stage": self.stage, "stats": snap}, self.engine)
        total = max(snap.get("domains_total", 0), 1)
        if self.stage == "searching":
            q_total = max(snap.get("queries_total", 1), 1)
            self.ctx.progress(snap.get("queries_done", 0), q_total, f"Searching ({snap.get('queries_done', 0)}/{q_total} queries)", force=force)
        else:
            self.ctx.progress(snap.get("domains_done", 0), total,
                              f"Crawling ({snap.get('domains_done', 0)}/{total} sites, {snap.get('leads_new', 0)} leads)", force=force)

    def domain(self, domain: str, state: str, pages: int = 0, emails: int = 0, n_leads: int = 0, note: str = "") -> None:
        with self.engine.begin() as c:
            c.execute(text("UPDATE run_domains SET state=:s, pages=:p, emails=:e, leads=:l, note=:n WHERE run_id=:r AND domain=:d"),
                      {"s": state, "p": pages, "e": emails, "l": n_leads, "n": note or None, "r": self.id, "d": domain})
        events.emit("run_domain", {"run_id": self.id, "domain": domain, "state": state, "pages": pages,
                                   "emails": emails, "leads": n_leads, "note": note}, self.engine)


def _known_domains(run_id: int, engine: Engine) -> set[str]:
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT DISTINCT rd.domain FROM run_domains rd JOIN search_runs sr ON sr.id = rd.run_id "
            "WHERE rd.state = 'done' AND rd.run_id != :r AND sr.started_at > datetime('now', :d)"),
            {"r": run_id, "d": f"-{KNOWN_DOMAIN_DAYS} days"})
        return {r[0] for r in rows}


def _process_site(run: _Run, cand: discovery.Candidate, plan: Plan, settings: AppSettings, fetcher: crawler.Fetcher,
                  mx: validator.MxChecker, people_fn: extractor.PeopleFn | None, state: dict, cancelled) -> None:
    domain = cand.domain
    run.domain(domain, "crawling")
    site_domains = {domain}
    drafts: list[LeadDraft] = []
    all_people: list[tuple[extractor.Person, str]] = []
    person_source: dict[str, str] = {}
    pages = emails = 0
    company = cand.osm_name or ""
    try:
        for page in crawler.iter_site_pages(cand.url, plan.mode, fetcher, settings.max_pages_per_domain, cancelled):
            pages += 1
            run.bump(pages=1)
            site_domains.add(domains.registrable(page.url))
            fn = people_fn if not state.get("llm_down") else None
            r = extractor.extract_page(page, site_domains, settings, people_fn=fn, domain=domain, company=company)
            run.bump(llm_calls=r.llm_calls, emails_found=r.emails_found, people_found=len(r.people))
            emails += r.emails_found
            drafts.extend(r.drafts)
            for p, ctx in r.people:
                all_people.append((p, ctx))
                person_source.setdefault(p.name, page.url)
            company = company or extractor.company_name(page.html, domain)  # from the first (home) page
            path = urlsplit(page.url).path or "/"
            if r.emails_found or r.people:
                run.log(f"{domain}{path}: {r.emails_found} email(s), {len(r.people)} people")
            run.push()
            if cancelled():
                break
    except llm.LlmError as e:  # pragma: no cover - extract_page already swallows these
        run.log(f"{domain}: model error ({str(e)[:80]})")

    if cand.osm_email and domains.looks_like_email(cand.osm_email.lower()) and not domains.is_junk_email(cand.osm_email.lower()):
        e = cand.osm_email.lower()
        drafts.append(LeadDraft(email=e, company=cand.osm_name or company, domain=domain, source_url=cand.url,
                                context_snippet=f"Listed in OpenStreetMap as {cand.osm_name or domain}",
                                email_confidence=validator.classify_confidence(e)))

    # Same address found on several pages: keep the richest draft.
    best: dict[str, LeadDraft] = {}
    for d in drafts:
        cur = best.get(d.email)
        if cur is None or (d.name and not cur.name) or (d.role and not cur.role):
            best[d.email] = d
    kept = list(best.values())
    kept += extractor.make_guesses(all_people, kept, domain, company, lambda p: person_source.get(p.name, cand.url),
                                   plan.target_roles)

    if not pages and not kept:
        run.bump(domains_failed=1, domains_done=1)
        run.domain(domain, "failed", note="Could not fetch (blocked by robots.txt, unreachable, or not HTML)")
        run.log(f"{domain}: no pages fetched")
        run.push(force=True)
        return

    # A big company's contact page can list dozens of regional mailboxes: keep the best few.
    trimmed = extractor.select_best(kept, plan.mode.value, settings.max_leads_per_site, plan.target_roles)
    if len(kept) > len(trimmed):
        run.bump(leads_trimmed=len(kept) - len(trimmed))
        run.log(f"{domain}: kept the best {len(trimmed)} of {len(kept)} addresses")
    kept = trimmed

    # Validate: MX per distinct domain; drop failures.
    checked: list[LeadDraft] = []
    for d in kept:
        ok = mx.check_email(d.email)
        if ok is True:
            checked.append(d)
        else:
            run.bump(leads_dropped=1)
    result = leads.ingest(checked, run_id=run.id, mode=plan.mode.value, engine=run.engine)
    for row in result.new:
        run.conf[row["email_confidence"]] += 1
    run.bump(leads_new=len(result.new), leads_dup=result.duplicates, leads_suppressed=result.suppressed,
             domains_done=1, leads_checked=len(kept))
    run.domain(domain, "done", pages=pages, emails=emails, n_leads=len(result.new))
    if result.new:
        run.log(f"{domain}: +{len(result.new)} lead(s)")
    run.push(force=True)


def _make_people_fn(settings: AppSettings, run: "_Run", state: dict) -> extractor.PeopleFn | None:
    """The model-backed people extractor, or None (emails only) if Ollama is down. One failure turns it off."""
    if not llm.is_running():
        run.log("Ollama is not running: extracting emails only (no names or roles).")
        return None
    base = extractor.llm_people_fn(settings)

    def guarded(text_: str, domain: str) -> list[extractor.Person]:
        try:
            return base(text_, domain)
        except llm.LlmError:
            if not state.get("llm_down"):
                state["llm_down"] = True
                run.log("The language model stopped responding; continuing with emails only.")
            raise

    return guarded


def run_discovery(ctx: JobContext, payload: dict) -> dict:
    """Job handler. Cancel stops cleanly and keeps everything found so far."""
    engine = ctx.engine
    run_id = int(payload["run_id"])
    opts = RunOptions(**payload.get("options", {}))
    row = get_run(run_id, engine)
    if row is None:
        raise ValueError(f"Run {run_id} not found")
    plan = Plan.model_validate(row["plan"])
    settings = db.load_settings(engine)
    run = _Run(run_id, engine, ctx)
    cancelled = _Cancel(ctx)
    started = time.monotonic()
    fetcher = crawler.Fetcher(timeout_s=settings.fetch_timeout_s,
                              delay_s=(settings.crawl_delay_min_s, settings.crawl_delay_max_s))
    mx = validator.MxChecker()
    final_state = "succeeded"
    try:
        run.push(force=True)
        run.log(f"Starting: {row['request_text']} ({plan.mode.value})")

        llm_state: dict = {}
        people_fn = _make_people_fn(settings, run, llm_state)
        seen: set[str] = set() if opts.recrawl else _known_domains(run_id, engine)
        cap = opts.max_domains or settings.max_domains_per_run
        crawled = 0
        used_queries: set[str] = set()
        queries = list(plan.search_queries)
        searcher = discovery.make_searcher(settings, plan)

        for round_no in range(1, MAX_ROUNDS + 1):
            run.stage = "searching"
            if round_no > 1:
                run.log(f"Round {round_no}: {run.stats['leads_new']} of {settings.target_leads} leads so far, searching with new wording…")
            used_queries |= {q.lower() for q in queries}
            run.bump(queries_total=len(queries), rounds=1)
            run.push(force=True)

            def on_query(i: int, n: int, q: str, found: int) -> None:
                run.bump(queries_done=1, search_hits=found)
                run.log(f"Searched “{q}”: {found} results")
                run.push()

            hits = discovery.run_searches(plan.model_copy(update={"search_queries": queries}), searcher,
                                          on_query=on_query, cancelled=cancelled)
            candidates = discovery.rank_candidates(hits, settings)
            if opts.expand_lists and not cancelled():
                run.log("Following list pages (e.g. “Top 50 startups in …”) to find company sites…")
                listed = discovery.expand_list_pages(
                    hits, fetcher.get, settings, cancelled=cancelled, max_pages=8 if round_no == 1 else 12,
                    on_page=lambda url, n: run.log(f"List page {urlsplit(url).netloc}{urlsplit(url).path[:40]}: {n} company links"))
                have = {c.domain for c in candidates}
                from_lists = [c for c in listed if c.domain not in have]
                run.log(f"{len(from_lists)} extra sites from list pages")
                candidates = discovery.interleave(candidates, from_lists)
            if round_no == 1 and discovery.wants_overpass(plan, opts.use_overpass) and not cancelled():
                run.log("Querying OpenStreetMap for local businesses…")
                osm = discovery.overpass_candidates(plan)
                have = {c.domain for c in candidates}
                candidates += [c for c in osm if c.domain not in have and not domains.is_blocked(c.domain, settings.domain_blocklist_extra)]
                run.log(f"OpenStreetMap: {len(osm)} businesses with a website")
            if cancelled():
                raise JobCancelled()

            fresh = [c for c in candidates if c.domain not in seen]
            skipped = len(candidates) - len(fresh)
            fresh = fresh[: max(cap - crawled, 0)]
            seen |= {c.domain for c in fresh}
            crawled += len(fresh)
            run.bump(domains_total=len(fresh))
            run.log(f"{len(hits)} results → {len(fresh)} sites to crawl"
                    + (f" ({skipped} skipped: already crawled)" if skipped else ""))
            with engine.begin() as c:
                for cand in fresh:
                    c.execute(text("INSERT OR IGNORE INTO run_domains (run_id, domain, url) VALUES (:r, :d, :u)"),
                              {"r": run_id, "d": cand.domain, "u": cand.url})
            run.stage = "crawling"
            run.push(force=True)

            def work(cand: discovery.Candidate) -> None:
                if cancelled():
                    run.domain(cand.domain, "skipped", note="Cancelled")
                    return
                if time.monotonic() - started > MAX_RUNTIME_S:
                    run.domain(cand.domain, "skipped", note="Time budget reached")
                    return
                try:
                    _process_site(run, cand, plan, settings, fetcher, mx, people_fn, llm_state, cancelled)
                except Exception as e:  # noqa: BLE001 - one bad site must not stop the run
                    log.exception("site %s failed", cand.domain)
                    run.bump(domains_failed=1, domains_done=1)
                    run.domain(cand.domain, "failed", note=str(e)[:120])
                    run.push(force=True)

            with ThreadPoolExecutor(max_workers=settings.crawl_concurrency, thread_name_prefix="crawl") as pool:
                list(pool.map(work, fresh))

            # Widen only if we are short of the target and there is budget, room and wording left.
            if (cancelled() or settings.target_leads <= 0 or run.stats["leads_new"] >= settings.target_leads
                    or crawled >= cap or time.monotonic() - started > WIDEN_UNTIL_S):
                break
            queries = planner.more_queries(plan, used_queries)
            if not queries:
                run.log("No more search wording to try.")
                break

        if cancelled():
            final_state = "cancelled"
        elif time.monotonic() - started > MAX_RUNTIME_S:
            run.log("Stopped at the time budget; leads found so far are saved.")
    except JobCancelled:
        final_state = "cancelled"
    except Exception as e:
        run.stage = "failed"
        with engine.begin() as c:
            c.execute(text("UPDATE search_runs SET state='failed', error=:e, finished_at=datetime('now') WHERE id=:i"),
                      {"e": str(e)[:300], "i": run_id})
        run.push(force=True, state="failed")
        raise
    finally:
        fetcher.close()

    run.stage = "done"
    snap = run.snapshot()
    csv_path = None
    try:
        config.exports_dir().mkdir(parents=True, exist_ok=True)
        csv_path = config.exports_dir() / f"leads-run-{run_id}.csv"
        csv_path.write_text(exports.export_csv(leads.LeadFilter(run_id=run_id), "company:asc", engine))
    except OSError as e:
        run.log(f"Could not write the CSV export: {e}")
    with engine.begin() as c:
        c.execute(text("UPDATE search_runs SET state=:s, stage='done', finished_at=datetime('now'), stats_json=:j WHERE id=:i"),
                  {"s": final_state, "j": json.dumps(snap), "i": run_id})
    verb = "Cancelled" if final_state == "cancelled" else "Finished"
    run.log(f"{verb}: {snap.get('leads_new', 0)} new leads from {snap.get('domains_done', 0)} sites in "
            f"{int(time.monotonic() - started)}s")
    run.push(force=True, state=final_state)
    if final_state == "cancelled":
        raise JobCancelled()
    return {"run_id": run_id, "leads_new": snap.get("leads_new", 0), "csv": str(csv_path) if csv_path else None}
