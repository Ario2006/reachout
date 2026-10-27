"""The crawl engine: many domains at once, pipelined stages, bounded queues.

    candidates -> [domain workers: fetch + analyse] -> bounded queue -> [finishers: merge, MX, store]
                                         \\-> LLM batcher (single consumer, off the crawl's critical path)

A domain worker owns one site at a time (one request at a time, politely spaced by the fetcher). Finished
sites wait in a bounded queue, so memory stays flat however slow the model or the database is."""
from __future__ import annotations

import asyncio
import logging
import queue
import resource
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from backend.core import analysis, crawlplan, discovery, domains, extractor, leads, llm
from backend.core.afetch import AsyncFetcher, Fetched
from backend.core.analysis import PageAnalysis
from backend.core.extractor import LeadDraft, Person
from backend.core.llmqueue import PeopleBatcher
from backend.core.models import AppSettings
from backend.core.planner import Plan
from backend.core.store import CrawlStore
from backend.core.validator import MxChecker

log = logging.getLogger("reachout.engine")
FINISH_QUEUE = 32
FINISHERS = 3
CANCEL_POLL_S = 0.5


class Cancelled(Exception):
    """The run was cancelled; everything stored so far is kept."""


class Emitter:
    """Runs small database writes and event emissions on one thread, in order, so the event loop never waits."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._t = threading.Thread(target=self._loop, name="emitter", daemon=True)
        self._t.start()

    def __call__(self, fn, *args, **kw) -> None:  # noqa: ANN001, ANN002, ANN003
        self._q.put((fn, args, kw))

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                return
            fn, args, kw = item
            try:
                fn(*args, **kw)
            except Exception:  # noqa: BLE001 - observability must never break a crawl
                log.exception("emit failed")
            finally:
                self._q.task_done()

    def flush(self) -> None:
        self._q.join()

    def close(self) -> None:
        self._q.put(None)
        self._t.join(timeout=5)


@dataclass
class Metrics:
    """Per-run observability: where time went, what was fetched from where, and what failed."""
    started: float = field(default_factory=time.monotonic)
    stage_s: Counter = field(default_factory=Counter)
    failures: Counter = field(default_factory=Counter)
    pages_ok: int = 0
    early_stops: int = 0
    sitemaps: int = 0
    guesses: int = 0
    site_failures: Counter = field(default_factory=Counter)

    def page(self, f: Fetched) -> None:
        if f.ok:
            self.pages_ok += 1
        else:
            self.failures[(f.reason or "error").split(":")[0]] += 1


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(rss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)  # bytes on macOS, KB on Linux


@dataclass
class SiteJob:
    cand: discovery.Candidate
    analyses: list[PageAnalysis]
    tasks: list[asyncio.Task | None]
    pages: int
    emails: int
    company: str


@dataclass
class EngineCtx:
    run: object  # pipeline._Run (log / bump / domain / push)
    plan: Plan
    settings: AppSettings
    fetcher: AsyncFetcher
    batcher: PeopleBatcher | None
    mx: MxChecker
    store: CrawlStore
    cancel: asyncio.Event
    emit: Emitter
    io: ThreadPoolExecutor
    cpu: ThreadPoolExecutor
    metrics: Metrics = field(default_factory=Metrics)
    deadline: float = 0.0  # monotonic time after which no new site is started
    _last_push: float = 0.0

    @property
    def run_id(self) -> int:
        return self.run.id  # type: ignore[attr-defined]

    def push(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_push >= 0.25:
            self._last_push = now
            self.run.extra = self.extra_stats  # type: ignore[attr-defined]
            self.emit(self.run.push, force)  # type: ignore[attr-defined]

    def extra_stats(self) -> dict:
        """Observability numbers merged into the run's stats on every push."""
        m = self.metrics
        fs = self.fetcher.snapshot()
        crawl_s = max(time.monotonic() - m.started, 1e-6)
        out = {
            "engine": {
                "tiers": fs["tiers"], "cache_hits": fs["cache_hits"], "cache_total": fs["cache_total"],
                "cache_hit_rate": fs["cache_hit_rate"], "requests": fs["requests"], "retries": fs["retries"],
                "bytes": fs["bytes"], "breaker_tripped": fs["breaker_tripped"], "max_inflight": fs["max_inflight"],
                "failures": dict(m.failures), "site_failures": dict(m.site_failures), "early_stops": m.early_stops,
                "sitemaps_read": m.sitemaps, "blind_guesses": m.guesses,
                "pages_per_sec": round(m.pages_ok / crawl_s, 2),
                "stage_seconds": {k: round(v, 1) for k, v in m.stage_s.items()},
                "peak_rss_mb": peak_rss_mb(),
            }
        }
        if self.batcher is not None:
            b = self.batcher.snapshot()
            out["engine"]["llm"] = b
            out["llm_calls"] = b["calls"]
        return out

    async def blocking(self, fn, *args, pool: ThreadPoolExecutor | None = None):  # noqa: ANN001, ANN002
        """Run blocking code on a pool, returning early (abandoning it) if the run is cancelled."""
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(pool or self.io, fn, *args)
        cancel = asyncio.ensure_future(self.cancel.wait())
        try:
            done, _ = await asyncio.wait({fut, cancel}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            cancel.cancel()
        if fut in done:
            return fut.result()
        raise Cancelled()


# ---- one site ---------------------------------------------------------------------------------

def enough_contacts(analyses: list[PageAnalysis], max_leads: int) -> bool:
    """Stop crawling a site once it has given us what we can use: enough distinct addresses, or two
    people with their own addresses."""
    emails = {h.email for a in analyses for h in a.hits}
    named = {sp.email for a in analyses for sp in a.structured.people if sp.email}
    return len(emails) >= max_leads or len(named) >= 2


async def _sitemap_plan(c: EngineCtx, root: str, mode, limit: int) -> list[crawlplan.PlannedPage]:
    p = urlsplit(root)
    declared = await c.fetcher.sitemaps_for((p.hostname or "").lower(), p.scheme)
    first = [u for u in declared if domains.registrable(u) == domains.registrable(root)][:1] or [f"{p.scheme}://{p.netloc}/sitemap.xml"]
    urls: list[str] = []
    todo, fetched = list(first), 0
    while todo and fetched < 2:
        f = await c.fetcher.fetch(todo.pop(0), render_js=False)
        fetched += 1
        c.metrics.page(f) if not f.ok else None
        if not f.ok:
            continue
        pages, children = crawlplan.parse_sitemap(f.html)
        urls += pages
        if children and not pages:
            kids = [u for u in children if domains.registrable(u) == domains.registrable(root)]
            kids.sort(key=lambda u: (0 if "page" in u.lower() else 1, len(u)))  # page sitemaps before posts/products
            todo = kids[:1]
    if fetched:
        c.metrics.sitemaps += 1
    return crawlplan.plan_from_sitemap(root, urls, mode, limit)


async def crawl_domain(c: EngineCtx, cand: discovery.Candidate) -> SiteJob | None:
    s, mode = c.settings, c.plan.mode
    domain, run = cand.domain, c.run
    cap = s.max_pages_per_domain
    start = cand.url
    p = urlsplit(start)
    root = f"{p.scheme}://{p.netloc}/"
    site_domains = {domain}
    c.emit(run.domain, domain, "crawling")  # type: ignore[attr-defined]

    async def get(url: str, render: bool = True) -> Fetched:
        f = await c.fetcher.fetch(url, render_js=render)
        c.metrics.page(f)
        c.emit(c.store.set_url_state, c.run_id, domain, url, "done" if f.ok else "failed", f.tier, f.status, f.reason)
        return f

    home = await get(root)
    if not home.ok and crawlplan._norm(start) != crawlplan._norm(root):
        home = await get(start)  # the homepage is blocked or broken: try the page the search surfaced
    if not home.ok:
        c.metrics.site_failures[(home.reason or "error").split(":")[0]] += 1
        run.bump(domains_failed=1, domains_done=1)  # type: ignore[attr-defined]
        c.emit(run.domain, domain, "failed", 0, 0, 0, _why(home.reason))  # type: ignore[attr-defined]
        c.emit(run.log, f"{domain}: {_why(home.reason)}")  # type: ignore[attr-defined]
        c.push(force=True)
        return None

    analyses: list[PageAnalysis] = []
    tasks: list[asyncio.Task | None] = []
    company = cand.osm_name or ""

    async def take(f: Fetched) -> None:
        nonlocal company
        site_domains.add(domains.registrable(f.final_url))
        t = time.monotonic()
        a = await asyncio.get_running_loop().run_in_executor(
            c.cpu, lambda: analysis.analyze_page(f.final_url, f.html, site_domains, domain=domain, company=company,
                                                 tier=f.tier, fetched_at=c.store.now()))
        c.metrics.stage_s["extract"] += time.monotonic() - t
        company = company or a.company
        run.bump(pages=1, emails_found=len(a.hits), people_found=len(a.structured.people))  # type: ignore[attr-defined]
        analyses.append(a)
        if a.needs_llm and c.batcher is not None and not c.batcher.down:
            tasks.append(asyncio.ensure_future(c.batcher.submit(domain, a.url, a.llm_text)))
        else:
            tasks.append(None)
        if a.hits or a.structured.people:
            c.emit(run.log, f"{domain}{urlsplit(a.url).path or '/'}: {len(a.hits)} email(s), {len(a.structured.people)} people "  # type: ignore[attr-defined]
                            f"({f.tier}{', LLM' if tasks[-1] else ''})")
        c.push()

    await take(home)
    planned = crawlplan.plan_from_links(home.final_url, home.html, mode, cap - 1, hit_url=start)
    if sum(1 for pl in planned if pl.score >= 40) < 2:  # the site's own links offered little: try its sitemap
        planned = crawlplan.merge_plans(planned, await _sitemap_plan(c, root, mode, cap - 1), limit=cap - 1)
    if not planned:
        planned = crawlplan.guess_pages(root, mode, 3)[: cap - 1]  # last resort, at most three
        c.metrics.guesses += len(planned)

    for pl in planned:
        if c.cancel.is_set():
            raise Cancelled()
        if enough_contacts(analyses, s.max_leads_per_site):
            c.metrics.early_stops += 1
            break
        f = await get(pl.url)
        if f.ok:
            await take(f)

    return SiteJob(cand, analyses, tasks, pages=len(analyses), emails=sum(len(a.hits) for a in analyses), company=company)


def _why(reason: str | None) -> str:
    r = (reason or "error").split(":")[0]
    return {
        "robots": "blocked by robots.txt", "http_403": "the site refused us (403)", "blocked_captcha": "the site showed a bot check",
        "dns": "the domain does not resolve", "dead_domain": "recently unreachable (skipped)", "breaker": "skipped after repeated failures",
        "timeout": "timed out", "network": "network error", "http_404": "page not found", "rate_limited": "the site asked us to slow down",
        "crawl_delay_too_long": "robots.txt asks for a crawl delay over 10 s", "non_html": "not a web page", "ssrf": "not a public address",
    }.get(r, f"could not fetch ({r})")


# ---- finishing a site: merge, validate, store ---------------------------------------------------------

async def finish_site(c: EngineCtx, job: SiteJob) -> None:
    run, s, plan = c.run, c.settings, c.plan
    domain = job.cand.domain
    t0 = time.monotonic()
    people_lists: list[list[Person]] = []
    for task in job.tasks:
        ppl: list[Person] = []
        if task is not None:
            try:
                ppl = await task
            except llm.LlmError:
                if c.batcher is not None and c.batcher.down:
                    c.emit(run.log, "The language model stopped responding; continuing with heuristics only.")  # type: ignore[attr-defined]
        people_lists.append(ppl)
    run.bump(llm_pages=sum(1 for t in job.tasks if t is not None))  # type: ignore[attr-defined]

    results = [analysis.finalize_page(a, p) for a, p in zip(job.analyses, people_lists)]
    drafts: list[LeadDraft] = []
    people: list[tuple[Person, str]] = []
    source: dict[str, str] = {}
    for a, r in zip(job.analyses, results):
        drafts += r.drafts
        for pr, ctx in r.people:
            people.append((pr, ctx))
            source.setdefault(pr.name, a.url)
    if job.cand.osm_email and domains.looks_like_email(job.cand.osm_email.lower()) and not domains.is_junk_email(job.cand.osm_email.lower()):
        e = job.cand.osm_email.lower()
        from backend.core.validator import classify_confidence
        drafts.append(LeadDraft(email=e, company=job.cand.osm_name or job.company, domain=domain, source_url=job.cand.url,
                                context_snippet=f"Listed in OpenStreetMap as {job.cand.osm_name or domain}",
                                email_confidence=classify_confidence(e), page_type="osm",
                                trace=[("found", f"Listed in OpenStreetMap with the email {e}")]))

    best: dict[str, LeadDraft] = {}
    for d in drafts:
        cur = best.get(d.email)
        if cur is None:
            best[d.email] = d
        else:  # same address on several pages: keep the richest draft and remember every source
            keep, other = (d, cur) if ((d.name and not cur.name) or (d.role and not cur.role)) else (cur, d)
            keep.trace += [t for t in other.trace if t not in keep.trace]
            keep.trace.append(("source", f"Also found on {other.source_url}"))
            keep.also += other.also + [{"url": other.source_url, "tier": other.page_tier, "type": other.page_type,
                                        "fetched_at": other.fetched_at, "snippet": other.context_snippet}]
            best[d.email] = keep
    kept = list(best.values())
    for g in extractor.make_guesses(people, kept, domain, job.company, lambda pr: source.get(pr.name, job.cand.url), plan.target_roles):
        g.trace = [("found", f"{g.name} ({g.role}) is listed on {g.source_url}, but no address was published"),
                   ("label", f"Labelled guessed: built from the name and this domain's address pattern. Only the domain's mail server was checked. {g.notes}")]
        g.page_type = crawlplan.classify(g.source_url)
        kept.append(g)

    trimmed = extractor.select_best(kept, plan.mode.value, s.max_leads_per_site, plan.target_roles)
    if len(kept) > len(trimmed):
        run.bump(leads_trimmed=len(kept) - len(trimmed))  # type: ignore[attr-defined]
        c.emit(run.log, f"{domain}: kept the best {len(trimmed)} of {len(kept)} addresses")  # type: ignore[attr-defined]
        dropped = {d.email for d in kept} - {d.email for d in trimmed}
        for d in trimmed:
            d.trace.append(("rank", f"Kept as one of the best {len(trimmed)} addresses of {domain}; {len(dropped)} others were trimmed"))
    kept = trimmed

    # Validate: MX once per distinct domain, with a persisted negative cache.
    t1 = time.monotonic()
    checked: list[LeadDraft] = []
    mx_ok: dict[str, bool] = {}
    for d in kept:
        dom = domains.split_email(d.email)[1]
        if domains.is_disposable(dom):
            run.bump(leads_dropped=1)  # type: ignore[attr-defined]
            c.emit(run.log, f"{d.email}: dropped, {dom} is a disposable-mailbox provider")  # type: ignore[attr-defined]
            continue
        if dom not in mx_ok:
            if await c.blocking(c.store.no_mx, dom):
                mx_ok[dom] = False
            else:
                ok = await c.blocking(c.mx.has_mx, dom)
                mx_ok[dom] = ok is True
                if ok is False:
                    await c.blocking(c.store.mark_no_mx, dom)
        if mx_ok[dom]:
            d.trace.append(("mx", f"{dom} has a mail server (MX record)"))
            d.mx_ok = True
            checked.append(d)
        else:
            run.bump(leads_dropped=1)  # type: ignore[attr-defined]
    c.metrics.stage_s["validate"] += time.monotonic() - t1

    t2 = time.monotonic()
    result = await c.blocking(
        lambda: leads.ingest(checked, run_id=c.run_id, mode=plan.mode.value, target_roles=plan.target_roles, engine=c.run.engine))  # type: ignore[attr-defined]
    c.metrics.stage_s["store"] += time.monotonic() - t2
    for row in result.new:
        run.conf[row["email_confidence"]] += 1  # type: ignore[attr-defined]
    run.bump(leads_new=len(result.new), leads_dup=result.duplicates, leads_suppressed=result.suppressed,  # type: ignore[attr-defined]
             domains_done=1, leads_checked=len(kept))
    c.emit(run.domain, domain, "done", job.pages, job.emails, len(result.new))  # type: ignore[attr-defined]
    if result.new:
        c.emit(run.log, f"{domain}: +{len(result.new)} lead(s)")  # type: ignore[attr-defined]
    c.metrics.stage_s["finish"] += time.monotonic() - t0
    c.push(force=True)


# ---- driving a batch of sites --------------------------------------------------------------------------

async def crawl_candidates(c: EngineCtx, candidates: list[discovery.Candidate]) -> None:
    """Crawl and finish every candidate. Raises Cancelled if the run was cancelled (after cleaning up)."""
    if not candidates:
        return
    todo: asyncio.Queue = asyncio.Queue()
    for cand in candidates:
        todo.put_nowait(cand)
    finish_q: asyncio.Queue = asyncio.Queue(maxsize=FINISH_QUEUE)
    run = c.run
    t_crawl = time.monotonic()

    async def domain_worker() -> None:
        while True:
            try:
                cand = todo.get_nowait()
            except asyncio.QueueEmpty:
                return
            if time.monotonic() > c.deadline:
                c.emit(run.domain, cand.domain, "skipped", 0, 0, 0, "Time budget reached")  # type: ignore[attr-defined]
                continue
            try:
                job = await crawl_domain(c, cand)
            except Cancelled:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - one bad site must never stop the run
                log.exception("site %s failed", cand.domain)
                run.bump(domains_failed=1, domains_done=1)  # type: ignore[attr-defined]
                c.emit(run.domain, cand.domain, "failed", 0, 0, 0, str(e)[:120])  # type: ignore[attr-defined]
                c.push(force=True)
                continue
            if job is not None:
                await finish_q.put(job)  # blocks when finishers are behind: back-pressure keeps memory flat

    async def finisher() -> None:
        while True:
            job = await finish_q.get()
            if job is None:
                return
            try:
                await finish_site(c, job)
            except (Cancelled, asyncio.CancelledError):
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("finishing %s failed", job.cand.domain)
                run.bump(domains_failed=1, domains_done=1)  # type: ignore[attr-defined]
                c.emit(run.domain, job.cand.domain, "failed", job.pages, job.emails, 0, str(e)[:120])  # type: ignore[attr-defined]
                c.push(force=True)

    workers = [asyncio.create_task(domain_worker(), name=f"crawl-{i}") for i in range(min(c.settings.global_concurrency, len(candidates)))]
    finishers = [asyncio.create_task(finisher(), name=f"finish-{i}") for i in range(FINISHERS)]
    cancel_wait = asyncio.ensure_future(c.cancel.wait())

    async def drain() -> None:
        await asyncio.gather(*workers)
        for _ in finishers:
            await finish_q.put(None)
        await asyncio.gather(*finishers)

    main = asyncio.ensure_future(drain())
    try:
        done, _ = await asyncio.wait({main, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
        if main in done:
            main.result()
            return
        raise Cancelled()
    finally:
        cancel_wait.cancel()
        if not main.done():
            main.cancel()
        for t in workers + finishers:
            t.cancel()
        await asyncio.gather(main, *workers, *finishers, return_exceptions=True)
        c.metrics.stage_s["crawl"] += time.monotonic() - t_crawl
