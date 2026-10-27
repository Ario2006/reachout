"""The lead pipeline: plan -> search -> crawl -> extract -> validate -> store, as one job.
The crawl itself lives in `engine` (async, pipelined); this module owns the run: rounds, cancel, resume."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import Engine, text

from backend.core import afetch, config, db, discovery, domains, engine as eng, events, exports, jobs, leads, llm, planner, validator
from backend.core.jobs import JobCancelled, JobContext
from backend.core.llmqueue import PeopleBatcher
from backend.core.models import AppSettings
from backend.core.planner import Plan
from backend.core.store import CrawlStore

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


def resume_run(run_id: int, engine: Engine | None = None) -> dict:
    """Queue a cancelled or failed run to continue: unfinished sites only; finished work and the page cache are kept."""
    engine = engine or db.get_engine()
    run = get_run(run_id, engine)
    if run is None:
        raise ValueError("Run not found")
    if run["state"] not in ("cancelled", "failed"):
        raise ValueError(f"A {run['state']} run cannot be resumed")
    old = jobs.get_job(run["job_id"], engine) if run["job_id"] else None
    job = jobs.create_job("discovery_run", {"run_id": run_id, "options": (old or {}).get("payload", {}).get("options", {}), "resume": True}, engine)
    with engine.begin() as c:
        c.execute(text("UPDATE search_runs SET job_id=:j, state='queued', error=NULL, finished_at=NULL WHERE id=:i"), {"j": job["id"], "i": run_id})
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


class _Run:
    """Mutable run state shared by worker threads: stats, run row, log and events."""

    def __init__(self, run_id: int, engine: Engine, ctx: JobContext, initial: dict | None = None):
        self.id, self.engine, self.ctx = run_id, engine, ctx
        self.stats: Counter[str] = Counter({k: v for k, v in (initial or {}).items() if isinstance(v, int) and not isinstance(v, bool)})
        self.conf: Counter[str] = Counter((initial or {}).get("by_confidence", {}))
        self.extra: Callable[[], dict] | None = None  # observability numbers from the engine, merged into every snapshot
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
            snap = {**self.stats, "by_confidence": dict(self.conf)}
        if self.extra is not None:
            snap.update(self.extra())
        return snap

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


def pending_candidates(run_id: int, engine: Engine) -> list[discovery.Candidate]:
    """Sites of this run that were not finished (a cancelled or interrupted run): the ones to resume."""
    with engine.connect() as c:
        rows = c.execute(text("SELECT domain, url FROM run_domains WHERE run_id = :r AND state IN ('queued', 'crawling', 'skipped') ORDER BY id"),
                         {"r": run_id}).all()
    return [discovery.Candidate(domain=d, url=u or f"https://{d}/", score=1) for d, u in rows]


def make_fetcher(settings: AppSettings, store: CrawlStore) -> afetch.AsyncFetcher:
    ua = afetch.build_user_agent(settings.crawler_contact)
    browser = afetch.BrowserTier(user_agent=ua) if settings.browser_tier_enabled else None
    return afetch.AsyncFetcher(
        store, delay_s=(settings.crawl_delay_min_s, settings.crawl_delay_max_s), total_timeout_s=settings.fetch_timeout_s,
        global_limit=settings.global_concurrency, ttl_s=settings.page_cache_ttl_days * 86400, user_agent=ua, browser=browser)


def make_batcher(settings: AppSettings) -> PeopleBatcher | None:
    return PeopleBatcher(settings) if llm.is_running() else None


def make_mx() -> validator.MxChecker:
    return validator.MxChecker()


async def _run_async(ctx: JobContext, run: _Run, plan: Plan, opts: RunOptions, settings: AppSettings, row: dict,
                     resume: bool) -> str:
    """The run proper. Returns the final state ('succeeded' or 'cancelled'); raises on a real failure."""
    engine = ctx.engine
    started = time.monotonic()
    cancel = asyncio.Event()
    emit = eng.Emitter()
    io = ThreadPoolExecutor(8, thread_name_prefix="io")
    cpu = ThreadPoolExecutor(2, thread_name_prefix="cpu")
    store = CrawlStore(engine)
    fetcher = make_fetcher(settings, store)
    batcher = make_batcher(settings)
    ec = eng.EngineCtx(run=run, plan=plan, settings=settings, fetcher=fetcher, batcher=batcher, mx=make_mx(), store=store,
                       cancel=cancel, emit=emit, io=io, cpu=cpu, deadline=started + MAX_RUNTIME_S)

    async def watch_cancel() -> None:
        while not cancel.is_set():
            if await asyncio.get_running_loop().run_in_executor(io, ctx.cancelled):
                cancel.set()
                return
            await asyncio.sleep(eng.CANCEL_POLL_S)

    watcher = asyncio.ensure_future(watch_cancel())
    final_state = "succeeded"
    try:
        run.push(force=True)
        run.log(f"{'Resuming' if resume else 'Starting'}: {row['request_text']} ({plan.mode.value})")
        if batcher is None:
            run.log("Ollama is not running: names and roles come from page structure only (no language model).")

        seen: set[str] = set() if opts.recrawl else _known_domains(run.id, engine)
        cap = opts.max_domains or settings.max_domains_per_run
        crawled = 0
        used_queries: set[str] = set()
        queries = list(plan.search_queries)
        searcher = discovery.make_searcher(settings, plan)
        pending = pending_candidates(run.id, engine) if resume else []
        if resume:
            with engine.connect() as c:
                crawled = int(c.execute(text("SELECT COUNT(*) FROM run_domains WHERE run_id = :r"), {"r": run.id}).scalar_one())
                seen |= {r[0] for r in c.execute(text("SELECT domain FROM run_domains WHERE run_id = :r"), {"r": run.id})}
            run.log(f"{len(pending)} site(s) left to crawl from the interrupted run")

        for round_no in range(1, MAX_ROUNDS + 1):
            fresh: list[discovery.Candidate]
            if resume and round_no == 1:
                run.stage = "crawling"
                fresh = pending
                used_queries |= {q.lower() for q in queries}
            else:
                run.stage = "searching"
                if round_no > 1:
                    run.log(f"Round {round_no}: {run.stats['leads_new']} of {settings.target_leads} leads so far, searching with new wording…")
                used_queries |= {q.lower() for q in queries}
                run.bump(queries_total=len(queries), rounds=1)
                run.push(force=True)

                def on_query(i: int, n: int, q: str, found: int) -> None:
                    run.bump(queries_done=1, search_hits=found)
                    ec.emit(run.log, f"Searched “{q}”: {found} results")
                    ec.push()

                t_search = time.monotonic()
                hits = await ec.blocking(lambda: discovery.run_searches(
                    plan.model_copy(update={"search_queries": queries}), searcher, on_query=on_query, cancelled=cancel.is_set))
                candidates = discovery.rank_candidates(hits, settings)
                if opts.expand_lists and not cancel.is_set():
                    run.log("Following list pages (e.g. “Top 50 startups in …”) to find company sites…")
                    listed = await discovery.expand_list_pages_async(
                        hits, lambda u: fetcher.fetch(u, render_js=False), settings, cancelled=cancel.is_set,
                        max_pages=8 if round_no == 1 else 12,
                        on_page=lambda url, n: ec.emit(run.log, f"List page {urlsplit(url).netloc}{urlsplit(url).path[:40]}: {n} company links"))
                    have = {c.domain for c in candidates}
                    from_lists = [c for c in listed if c.domain not in have]
                    run.log(f"{len(from_lists)} extra sites from list pages")
                    candidates = discovery.interleave(candidates, from_lists)
                if round_no == 1 and discovery.wants_overpass(plan, opts.use_overpass) and not cancel.is_set():
                    run.log("Querying OpenStreetMap for local businesses…")
                    osm = await ec.blocking(discovery.overpass_candidates, plan)
                    have = {c.domain for c in candidates}
                    candidates += [c for c in osm if c.domain not in have and not domains.is_blocked(c.domain, settings.domain_blocklist_extra)]
                    run.log(f"OpenStreetMap: {len(osm)} businesses with a website")
                ec.metrics.stage_s["search"] += time.monotonic() - t_search
                if cancel.is_set():
                    raise eng.Cancelled()
                fresh = [c for c in candidates if c.domain not in seen]
                skipped = len(candidates) - len(fresh)
                fresh = fresh[: max(cap - crawled, 0)]
                seen |= {c.domain for c in fresh}
                crawled += len(fresh)
                run.bump(domains_total=len(fresh))
                run.log(f"{len(hits)} results → {len(fresh)} sites to crawl" + (f" ({skipped} skipped: already crawled)" if skipped else ""))

                def add_rows(rows: list[discovery.Candidate] = fresh) -> None:
                    with engine.begin() as c:
                        for cand in rows:
                            c.execute(text("INSERT OR IGNORE INTO run_domains (run_id, domain, url) VALUES (:r, :d, :u)"),
                                      {"r": run.id, "d": cand.domain, "u": cand.url})

                await ec.blocking(add_rows)
            run.stage = "crawling"
            ec.push(force=True)
            await eng.crawl_candidates(ec, fresh)

            # Widen only if we are short of the target and there is budget, room and wording left.
            if (cancel.is_set() or settings.target_leads <= 0 or run.stats["leads_new"] >= settings.target_leads
                    or crawled >= cap or time.monotonic() - started > WIDEN_UNTIL_S):
                break
            queries = planner.more_queries(plan, used_queries)
            if not queries:
                run.log("No more search wording to try.")
                break
        if time.monotonic() - started > MAX_RUNTIME_S:
            run.log("Stopped at the time budget; leads found so far are saved.")
    except eng.Cancelled:
        final_state = "cancelled"
    finally:
        watcher.cancel()
        ec.push(force=True)
        if batcher is not None:
            await batcher.close()
        await fetcher.aclose()
        await asyncio.get_running_loop().run_in_executor(None, emit.flush)
        emit.close()
        io.shutdown(wait=False, cancel_futures=True)
        cpu.shutdown(wait=False, cancel_futures=True)
    return final_state


def run_discovery(ctx: JobContext, payload: dict) -> dict:
    """Job handler. Cancel stops cleanly and keeps everything found so far; a cancelled or interrupted run can resume."""
    engine = ctx.engine
    run_id = int(payload["run_id"])
    opts = RunOptions(**payload.get("options", {}))
    resume = bool(payload.get("resume"))
    row = get_run(run_id, engine)
    if row is None:
        raise ValueError(f"Run {run_id} not found")
    plan = Plan.model_validate(row["plan"])
    settings = db.load_settings(engine)
    run = _Run(run_id, engine, ctx, initial=row["stats"] if resume else None)
    started = time.monotonic()
    try:
        final_state = asyncio.run(_run_async(ctx, run, plan, opts, settings, row, resume))
    except JobCancelled:
        final_state = "cancelled"
    except Exception as e:
        run.stage = "failed"
        with engine.begin() as c:
            c.execute(text("UPDATE search_runs SET state='failed', error=:e, finished_at=datetime('now') WHERE id=:i"),
                      {"e": str(e)[:300], "i": run_id})
        run.push(force=True, state="failed")
        raise

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
