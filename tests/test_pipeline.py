import asyncio
import time

import httpx
import pytest
from sqlalchemy import text

from backend.core import config, db, discovery, engine as eng, events, jobs, leads, llm, pipeline, validator
from backend.core.afetch import AsyncFetcher
from backend.core.extractor import Person
from backend.core.job_handlers import HANDLERS
from backend.core.llmqueue import BatchPeople, PagePeople, PeopleBatcher
from backend.core.models import AppSettings
from backend.core.planner import Plan

REAL_MX = validator.MxChecker
TEAM = """<html><head><title>Acme AI | Team</title></head><body>
<p>Priya Sharma, Co-founder &amp; CEO <a href="mailto:priya@acme.ai">priya@acme.ai</a></p>
<p>Rahul Verma, CTO</p><p>Contact: careers@acme.ai</p></body></html>"""
CARDS = """<html><head><title>Cards Co</title></head><body><h1>Team</h1><div class="g">
<div class="c"><h3>Ann Archer</h3><p>CEO</p><a href="mailto:ann.archer@cards.io">e</a></div>
<div class="c"><h3>Bob Brown</h3><p>CTO</p></div><div class="c"><h3>Cy Clark</h3><p>COO</p></div></div></body></html>"""
BASE_WEB = {
    "acme.ai/": '<title>Acme AI | Home</title><a href="/team">Team</a>', "acme.ai/team": TEAM,
    "only-info.com/": '<a href="mailto:hello@only-info.com">hi</a>',
    "nomx.ai/": '<a href="mailto:hi@nomx.ai">hi</a>',
    "robots-no.com/robots.txt": "User-agent: *\nDisallow: /", "robots-no.com/": "<a href='mailto:x@robots-no.com'>x</a>",
    "suppressed.io/": '<a href="mailto:ceo@suppressed.io">x</a><a href="mailto:info@suppressed.io">y</a>',
}
HITS = ["https://acme.ai/about", "https://only-info.com/", "https://nomx.ai/", "https://robots-no.com/", "https://dead.com/",
        "https://suppressed.io/", "https://linkedin.com/in/x", "https://acme.ai/team"]
PEOPLE = {"acme.ai": [Person(name="Priya Sharma", role="Co-founder & CEO"), Person(name="Rahul Verma", role="CTO"),
                      Person(name="Ghost Writer", role="CTO")]}


class Web:
    """A scripted fake web for the async fetcher: 'host/path' -> html string, Response, or callable(request)."""

    def __init__(self, routes=None):
        self.routes = dict(routes if routes is not None else BASE_WEB)
        self.requests: list[str] = []
        self.on_request = None
        self.delay = 0.0

    def paths(self, host):
        return [r.split(host, 1)[1].split("?")[0] or "/" for r in self.requests if f"//{host}" in r]

    async def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(str(req.url))
        if self.on_request:
            self.on_request(req)
        if self.delay:
            await asyncio.sleep(self.delay)
        r = self.routes.get(f"{req.url.host}{req.url.path}")
        if r is None:
            return httpx.Response(404, text="nope", headers={"content-type": "text/html"})
        if callable(r):
            r = r(req)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, str):
            return httpx.Response(200, text=r, headers={"content-type": "text/html"})
        return r


class FakeLlm:
    """The model: answers per page from PEOPLE, records every call. Never runs two calls at once."""

    def __init__(self, people=None):
        self.people = PEOPLE if people is None else people
        self.calls: list[int] = []
        self.fail = False
        self.active = self.max_active = 0

    async def __call__(self, body, n):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            self.calls.append(n)
            if self.fail:
                raise llm.LlmError("model crashed")
            pages = []
            for i in range(1, n + 1):
                site = body.split(f"### PAGE {i} (site: ")[1].split(")")[0]
                pages.append(PagePeople(page=i, people=self.people.get(site, [])))
            return BatchPeople(pages=pages)
        finally:
            self.active -= 1


@pytest.fixture
def world(monkeypatch, engine):
    web, fake = Web(), FakeLlm()
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url=u, query=q) for u in HITS])
    monkeypatch.setattr(pipeline, "make_fetcher", lambda s, store: AsyncFetcher(
        store, client=httpx.AsyncClient(transport=httpx.MockTransport(web), follow_redirects=True), delay_s=(0, 0),
        check_public=False, global_limit=s.global_concurrency))
    monkeypatch.setattr(pipeline, "make_mx", lambda: REAL_MX(lookup=lambda d: d != "nomx.ai"))
    monkeypatch.setattr(pipeline, "make_batcher", lambda s: PeopleBatcher(s, call=fake, max_wait_s=0.0))
    db.save_settings(AppSettings(target_leads=0), engine)  # single round unless a test widens
    return type("World", (), {"engine": engine, "web": web, "llm": fake})()


def start(engine, recrawl=False, queries=("q1", "q2"), roles=("CTO",), mode="internship", **opt):
    plan = Plan(mode=mode, target_roles=list(roles), search_queries=list(queries))
    run = pipeline.create_run("AI internships in Bangalore", plan, pipeline.RunOptions(recrawl=recrawl, **opt), engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    return run["id"], jobs.get_job(run["job_id"], engine)


def by_email(engine, run_id=None):
    return {r["email"]: r for r in leads.query_leads(leads.LeadFilter(run_id=run_id), engine=engine, limit=5000)[0]}


def stats(engine, run_id):
    return pipeline.get_run(run_id, engine)["stats"]


def test_end_to_end_run(world):
    engine = world.engine
    db.add_suppression("ceo@suppressed.io", "opt_out", engine)
    run_id, job = start(engine)
    assert job["state"] == "succeeded" and job["result"]["leads_new"] == 5
    got = by_email(engine, run_id)
    p = got["priya@acme.ai"]
    assert (p["name"], p["role"], p["email_confidence"], p["company"]) == ("Priya Sharma", "Co-founder & CEO", "published_personal", "Acme AI")
    assert p["source_url"] == "https://acme.ai/team" and p["mode"] == "internship" and p["search_run_id"] == run_id
    assert got["careers@acme.ai"]["email_confidence"] == "published_generic"
    assert got["hello@only-info.com"]["email_confidence"] == "published_generic"
    assert got["info@suppressed.io"]["email_confidence"] == "published_generic"
    assert got["rahul@acme.ai"]["email_confidence"] == "guessed"  # a listed CTO with no published address
    assert not any("ghost" in e for e in got)  # the model's invented name never reaches a lead
    for absent in ("hi@nomx.ai", "x@robots-no.com", "ceo@suppressed.io"):
        assert absent not in got


def test_run_record_domains_log_events_stats_and_csv(world):
    engine = world.engine
    run_id, _ = start(engine)
    run = pipeline.get_run(run_id, engine)
    assert run["state"] == "succeeded" and run["stage"] == "done" and run["finished_at"]
    s = run["stats"]
    assert s["queries_total"] == 2 and s["queries_done"] == 2 and s["search_hits"] == 2 * len(HITS)
    assert s["domains_total"] == 6 and s["domains_done"] == 6 and s["leads_new"] == run["leads_found"]
    assert s["leads_dropped"] >= 1 and s["pages"] >= 5 and s["llm_calls"] >= 1
    assert sum(s["by_confidence"].values()) == s["leads_new"]

    e = s["engine"]  # observability: where pages came from, what failed, where time went
    assert e["tiers"]["http"] >= 5 and e["tiers"]["cache"] == 0 and e["cache_hit_rate"] == 0.0 and e["requests"] >= 6
    assert e["failures"]["robots"] == 1 and e["failures"]["http_404"] >= 1
    assert {"search", "crawl", "extract", "validate", "store"} <= set(e["stage_seconds"])
    assert e["pages_per_sec"] > 0 and e["peak_rss_mb"] > 0 and e["llm"]["calls"] >= 1 and e["llm"]["pages_per_call"] >= 1

    st = {d["domain"]: d["state"] for d in pipeline.run_domains(run_id, engine)}
    assert st["acme.ai"] == "done" and st["only-info.com"] == "done" and st["nomx.ai"] == "done"
    assert st["robots-no.com"] == "failed" and st["dead.com"] == "failed" and "linkedin.com" not in st
    notes = {d["domain"]: d["note"] for d in pipeline.run_domains(run_id, engine)}
    assert notes["robots-no.com"] == "blocked by robots.txt" and notes["dead.com"] == "page not found"

    msgs = [r["message"] for r in pipeline.run_log(run_id, engine=engine)]
    assert any("Searched" in m for m in msgs) and any("acme.ai" in m for m in msgs) and msgs[-1].startswith("Finished")
    kinds = {x["type"] for x in events.read_since(0, 5000, engine)}
    assert {"run", "run_domain", "run_log", "lead", "job"} <= kinds
    csv = (config.exports_dir() / f"leads-run-{run_id}.csv").read_text()
    assert csv.splitlines()[0].startswith("name,email") and "priya@acme.ai" in csv


def test_a_repeat_run_is_served_entirely_from_the_page_cache(world):
    engine = world.engine
    run1, _ = start(engine)
    n = len(world.web.requests)
    assert n > 0
    run2, job2 = start(engine, recrawl=True)
    assert job2["state"] == "succeeded"
    assert len(world.web.requests) == n  # not one request: pages, robots, sitemaps and 404 guesses all cached
    e = stats(engine, run2)["engine"]
    assert e["cache_hit_rate"] == 1.0 and e["tiers"]["http"] == 0 and e["tiers"]["cache"] >= 5 and e["requests"] == 0
    assert stats(engine, run2)["leads_new"] == 0 and stats(engine, run2)["leads_dup"] >= 4


def test_rerun_without_recrawl_skips_recently_crawled_sites(world):
    engine = world.engine
    start(engine)
    run2, _ = start(engine)
    assert stats(engine, run2)["domains_total"] == 2  # only the two failed sites are retried
    run3, _ = start(engine, recrawl=True)
    assert stats(engine, run3)["domains_total"] == 6


def test_max_domains_limit(world):
    run_id, _ = start(world.engine, max_domains=2)
    assert stats(world.engine, run_id)["domains_total"] == 2


def test_one_crashing_site_does_not_stop_the_run(world, monkeypatch):
    real = eng.crawl_domain

    async def flaky(c, cand):
        if cand.domain == "only-info.com":
            raise RuntimeError("boom")
        return await real(c, cand)

    monkeypatch.setattr(eng, "crawl_domain", flaky)
    run_id, job = start(world.engine)
    st = {d["domain"]: d for d in pipeline.run_domains(run_id, world.engine)}
    assert job["state"] == "succeeded" and st["only-info.com"]["state"] == "failed" and "boom" in st["only-info.com"]["note"]
    assert "priya@acme.ai" in by_email(world.engine, run_id)


@pytest.mark.slow
def test_a_server_error_site_is_recorded_and_the_run_goes_on(world):
    world.web.routes["only-info.com/"] = httpx.Response(500)
    run_id, job = start(world.engine)
    st = {d["domain"]: d for d in pipeline.run_domains(run_id, world.engine)}
    assert job["state"] == "succeeded" and st["only-info.com"]["state"] == "failed"
    assert "priya@acme.ai" in by_email(world.engine, run_id)
    assert stats(world.engine, run_id)["engine"]["retries"] >= 2


def test_suppressed_addresses_never_become_leads(world):
    engine = world.engine
    db.add_suppression("info@suppressed.io", "bounce", engine)
    db.add_suppression("priya@acme.ai", "opt_out", engine)
    run_id, _ = start(engine)
    got = by_email(engine, run_id)
    assert "info@suppressed.io" not in got and "priya@acme.ai" not in got
    assert stats(engine, run_id)["leads_suppressed"] >= 2


def test_progress_events_reach_the_job(world):
    _, job = start(world.engine)
    msgs = [e["payload"]["message"] for e in events.read_since(0, 5000, world.engine) if e["type"] == "job" and e["payload"]["kind"] == "discovery_run"]
    assert any(m and m.startswith("Searching") for m in msgs) and any(m and m.startswith("Crawling") for m in msgs)
    assert job["progress"] == job["total"] == 6


# ---- the language model: heuristics first, batched, never required ---------------------------------

def test_without_ollama_the_run_still_works_from_page_structure(world, monkeypatch):
    monkeypatch.setattr(pipeline, "make_batcher", lambda s: None)
    run_id, job = start(world.engine)
    got = by_email(world.engine, run_id)
    assert job["state"] == "succeeded" and got["priya@acme.ai"]["email_confidence"] == "published_generic" and got["priya@acme.ai"]["name"] == ""
    assert not any(r["email_confidence"] == "guessed" for r in got.values())
    assert any("Ollama is not running" in m["message"] for m in pipeline.run_log(run_id, engine=world.engine))


def test_a_failing_model_degrades_gracefully(world):
    world.llm.fail = True
    run_id, job = start(world.engine)
    assert job["state"] == "succeeded" and "priya@acme.ai" in by_email(world.engine, run_id)


def test_team_cards_are_read_without_calling_the_model(world):
    world.web.routes.update({"cards.io/": '<title>Cards Co</title><a href="/team">t</a>', "cards.io/team": CARDS})
    monkey_hits = ["https://cards.io/"]
    discovery_search = lambda s, p: (lambda q: [discovery.SearchHit(url=u, query=q) for u in monkey_hits])  # noqa: E731
    import backend.core.discovery as d
    orig = d.make_searcher
    d.make_searcher = discovery_search
    try:
        run_id, _ = start(world.engine, queries=("q",), roles=("CTO", "COO"))
    finally:
        d.make_searcher = orig
    got = by_email(world.engine, run_id)
    assert world.llm.calls == []  # structure was enough
    assert got["ann.archer@cards.io"]["name"] == "Ann Archer" and got["ann.archer@cards.io"]["role"] == "CEO"
    assert got["ann.archer@cards.io"]["email_confidence"] == "published_personal"
    assert got["bob.brown@cards.io"]["email_confidence"] == "guessed" and got["bob.brown@cards.io"]["name"] == "Bob Brown"  # follows ann.archer@
    assert stats(world.engine, run_id)["engine"]["llm"]["calls"] == 0


def test_pages_that_need_the_model_share_batched_calls_one_at_a_time(world, monkeypatch):
    monkeypatch.setattr(pipeline, "make_batcher", lambda s: PeopleBatcher(s, call=world.llm, max_wait_s=0.3))
    for i in range(8):
        world.web.routes[f"t{i}.com/"] = f'<a href="/team">t</a>'
        world.web.routes[f"t{i}.com/team"] = f"<html><body><h1>Team</h1><p>Our founder Jane{i} Doe leads. " + "We build things. " * 30 + "</p></body></html>"
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url=f"https://t{i}.com/", query=q) for i in range(8)])
    start(world.engine, queries=("q",))
    assert world.llm.max_active == 1  # never two generations at once
    assert sum(world.llm.calls) == 8 and len(world.llm.calls) < 8  # 8 pages, fewer calls: they were batched


# ---- planning: stop early, sitemaps, blind guesses -----------------------------------------------------

def test_a_site_stops_early_once_it_has_enough_contacts(world, monkeypatch):
    many = "".join(f'<a href="mailto:office{i}@big.com">o</a>' for i in range(8)) + '<a href="/team">t</a><a href="/contact">c</a><a href="/careers">j</a>'
    world.web.routes.update({"big.com/": many, "big.com/team": "<p>team</p>", "big.com/contact": "<p>contact</p>", "big.com/careers": "<p>careers</p>"})
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url="https://big.com/", query=q)])
    run_id, _ = start(world.engine, queries=("q",))
    assert world.web.paths("big.com") == ["/robots.txt", "/"]  # 8 addresses on the homepage: nothing else was fetched
    assert stats(world.engine, run_id)["engine"]["early_stops"] == 1


def test_the_sitemap_is_used_when_the_homepage_links_nothing_useful(world, monkeypatch):
    world.web.routes.update({
        "sm.com/": "<html><body>Welcome. " + "We make things. " * 20 + "</body></html>",
        "sm.com/robots.txt": "User-agent: *\nSitemap: https://sm.com/sitemap.xml",
        "sm.com/sitemap.xml": "<urlset><url><loc>https://sm.com/leadership</loc></url><url><loc>https://sm.com/blog/a</loc></url></urlset>",
        "sm.com/leadership": '<p>Reach us: <a href="mailto:hello@sm.com">hello</a></p>'})
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url="https://sm.com/", query=q)])
    run_id, _ = start(world.engine, queries=("q",))
    paths = world.web.paths("sm.com")
    assert "/sitemap.xml" in paths and "/leadership" in paths and "/blog/a" not in paths
    assert not any(p in paths for p in ("/contact", "/about", "/team", "/careers"))  # no blind guessing when a sitemap helped
    e = stats(world.engine, run_id)["engine"]
    assert e["sitemaps_read"] == 1 and e["blind_guesses"] == 0 and "hello@sm.com" in by_email(world.engine, run_id)


def test_blind_guesses_are_a_last_resort_and_at_most_three(world, monkeypatch):
    world.web.routes["bare.com/"] = "<html><body>" + "Nothing linked here. " * 20 + "</body></html>"
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url="https://bare.com/", query=q)])
    run_id, _ = start(world.engine, queries=("q",))
    guessed = [p for p in world.web.paths("bare.com") if p not in ("/", "/robots.txt", "/sitemap.xml")]
    assert guessed == ["/careers", "/contact", "/about"] and stats(world.engine, run_id)["engine"]["blind_guesses"] == 3


def test_the_page_cap_per_site_is_respected(world, monkeypatch):
    links = "".join(f'<a href="/team{i}">t</a>' for i in range(10))
    world.web.routes["cap.com/"] = links + '<a href="mailto:a@cap.com">a</a>'
    for i in range(10):
        world.web.routes[f"cap.com/team{i}"] = "<p>x</p>"
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url="https://cap.com/", query=q)])
    db.save_settings(AppSettings(max_pages_per_domain=3, target_leads=0), world.engine)
    start(world.engine, queries=("q",))
    assert len([p for p in world.web.paths("cap.com") if p not in ("/robots.txt", "/sitemap.xml")]) <= 3


def test_a_site_contributes_only_its_best_addresses(world, monkeypatch):
    many = "".join(f'<a href="mailto:office{i}@big.com">o</a>' for i in range(20)) + '<a href="mailto:careers@big.com">c</a><a href="mailto:jobs@big.com">j</a>'
    world.web.routes["big.com/"] = many
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url="https://big.com/", query=q)])
    db.save_settings(AppSettings(max_leads_per_site=4, target_leads=0), world.engine)
    run_id, _ = start(world.engine, queries=("q",))
    got = by_email(world.engine, run_id)
    assert len(got) == 4 and {"careers@big.com", "jobs@big.com"} <= set(got)
    s = stats(world.engine, run_id)
    assert s["leads_trimmed"] == 18 and s["leads_new"] == 4
    assert any("kept the best 4 of 22" in m["message"] for m in pipeline.run_log(run_id, engine=world.engine))


# ---- cancel and resume ---------------------------------------------------------------------------------------

def _many_sites(world, monkeypatch, n=12):
    for i in range(n):
        world.web.routes[f"c{i}.com/"] = f'<a href="mailto:hello@c{i}.com">h</a>'
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url=f"https://c{i}.com/", query=q) for i in range(n)])


def test_cancel_stops_quickly_and_keeps_partial_results(world, monkeypatch):
    _many_sites(world, monkeypatch)
    world.web.delay = 0.05
    db.save_settings(AppSettings(global_concurrency=2, target_leads=0), world.engine)
    plan = Plan(mode="internship", target_roles=["CTO"], search_queries=["q"])
    run = pipeline.create_run("x", plan, pipeline.RunOptions(), world.engine)
    job_id = run["job_id"]
    seen = {"n": 0, "t": None}

    def trip(req):
        seen["n"] += 1
        if seen["n"] == 8 and seen["t"] is None:
            seen["t"] = time.monotonic()
            jobs.request_cancel(job_id, world.engine)

    world.web.on_request = trip
    jobs.run_job(jobs.claim_next(world.engine), HANDLERS, world.engine)
    took = time.monotonic() - seen["t"]
    assert jobs.get_job(job_id, world.engine)["state"] == "cancelled"
    r = pipeline.get_run(run["id"], world.engine)
    assert r["state"] == "cancelled" and r["finished_at"] and 0 < r["stats"]["domains_done"] < 12
    assert took < 2.5  # cancel takes effect within about two seconds
    assert "Cancelled" in pipeline.run_log(run["id"], engine=world.engine)[-1]["message"]
    assert len(by_email(world.engine, run["id"])) == r["stats"]["leads_new"] > 0  # what was found is kept


@pytest.mark.slow
def test_a_cancelled_run_resumes_where_it_stopped_without_refetching(world, monkeypatch):
    _many_sites(world, monkeypatch)
    world.web.delay = 0.02
    db.save_settings(AppSettings(global_concurrency=2, target_leads=0), world.engine)
    plan = Plan(mode="internship", target_roles=["CTO"], search_queries=["q"])
    run = pipeline.create_run("x", plan, pipeline.RunOptions(), world.engine)
    n = {"c": 0}

    def trip(req):
        n["c"] += 1
        if n["c"] == 10:
            jobs.request_cancel(run["job_id"], world.engine)

    world.web.on_request = trip
    jobs.run_job(jobs.claim_next(world.engine), HANDLERS, world.engine)
    first = pipeline.get_run(run["id"], world.engine)
    done_before = first["stats"]["domains_done"]
    leads_before = set(by_email(world.engine, run["id"]))
    assert first["state"] == "cancelled" and 0 < done_before < 12
    fetched_before = set(world.web.requests)

    world.web.on_request = None
    again = pipeline.resume_run(run["id"], world.engine)
    assert again["state"] == "queued"
    jobs.run_job(jobs.claim_next(world.engine), HANDLERS, world.engine)
    last = pipeline.get_run(run["id"], world.engine)
    assert last["state"] == "succeeded" and last["stats"]["domains_done"] == 12
    assert last["stats"]["domains_total"] == 12  # counters continued, not restarted
    assert len(by_email(world.engine, run["id"])) == 12 and leads_before <= set(by_email(world.engine, run["id"]))
    from collections import Counter
    twice = [u for u, n in Counter(world.web.requests).items() if n > 1]
    assert len(twice) <= 2  # only requests in flight at the cancel (one per worker, 2 workers) are ever repeated
    assert all(d["state"] == "done" for d in pipeline.run_domains(run["id"], world.engine))
    assert any("left to crawl from the interrupted run" in m["message"] for m in pipeline.run_log(run["id"], engine=world.engine))


def test_only_cancelled_or_failed_runs_can_resume(world):
    run_id, _ = start(world.engine)
    with pytest.raises(ValueError):
        pipeline.resume_run(run_id, world.engine)  # already succeeded
    with pytest.raises(ValueError):
        pipeline.resume_run(999, world.engine)


def test_per_url_state_is_persisted_for_the_run(world):
    run_id, _ = start(world.engine)
    from backend.core.store import CrawlStore
    states = CrawlStore(world.engine).url_states(run_id)
    assert states["https://acme.ai/"] == "done" and states["https://acme.ai/team"] == "done"
    assert states["https://robots-no.com/"] == "failed" and states["https://acme.ai/about"] == "failed"  # the blind guess 404ed


def test_reconcile_fixes_runs_whose_job_already_ended(engine):
    plan = Plan(mode="service", search_queries=["q"])
    crashed = pipeline.create_run("crashed", plan, pipeline.RunOptions(), engine)
    jobs.claim_next(engine)  # the worker "took" it...
    jobs.recover_stale(engine)  # ...then died; on restart its job is marked failed
    cancelled = pipeline.create_run("cancelled while queued", plan, pipeline.RunOptions(), engine)
    jobs.request_cancel(cancelled["job_id"], engine)
    waiting = pipeline.create_run("still waiting", plan, pipeline.RunOptions(), engine)
    assert pipeline.reconcile_runs(engine) == 2
    got = {r["id"]: r for r in pipeline.list_runs(engine=engine)}
    assert got[crashed["id"]]["state"] == "failed" and "restarted" in got[crashed["id"]]["error"] and got[crashed["id"]]["finished_at"]
    assert got[cancelled["id"]]["state"] == "cancelled" and got[cancelled["id"]]["finished_at"]
    assert got[waiting["id"]]["state"] == "queued"
    assert pipeline.reconcile_runs(engine) == 0  # idempotent


def test_a_new_search_can_start_after_a_crash(engine):
    from fastapi.testclient import TestClient
    from backend.api.main import create_app

    plan = {"mode": "service", "search_queries": ["q"]}
    app = create_app(token="t" * 32, allowed_origins=["http://localhost:5173"], engine=engine)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": "Bearer " + "t" * 32}) as c:
        r1 = c.post("/discovery/run", json={"request_text": "a", "plan": plan}).json()
        jobs.claim_next(engine)
        jobs.recover_stale(engine)
        assert c.get(f"/discovery/runs/{r1['id']}").json()["state"] == "failed"
        assert c.post("/discovery/run", json={"request_text": "b", "plan": plan}).status_code == 202
        assert [r["state"] for r in c.get("/discovery/runs").json()] == ["queued", "failed"]


# ---- widening rounds ---------------------------------------------------------------------------------------

@pytest.fixture
def widen(world, monkeypatch):
    asked: list[str] = []
    for i in range(12):
        world.web.routes[f"s{i}.com/"] = f'<title>Site {i}</title><a href="mailto:hello@s{i}.com">hi</a>'

    def searcher_for(settings, plan):
        def search(q):
            asked.append(q)
            n = 2 if q in ("first query", "second query") else 4
            start_ = 0 if n == 2 else (len(asked) - 2) * 2  # later queries surface different sites
            return [discovery.SearchHit(url=f"https://s{(start_ + i) % 12}.com/", query=q) for i in range(n)]
        return search

    monkeypatch.setattr(discovery, "make_searcher", searcher_for)
    return world.engine, asked


def go(engine, target, **opt):
    db.save_settings(AppSettings(target_leads=target, max_leads_per_site=5), engine)
    plan = Plan(mode="service", industries=["ai"], regions=["pune"], search_queries=["first query", "second query"])
    run = pipeline.create_run("x", plan, pipeline.RunOptions(expand_lists=False, **opt), engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    return run["id"], pipeline.get_run(run["id"], engine)


def test_widens_with_new_wording_until_the_target_is_met(widen):
    engine, asked = widen
    run_id, run = go(engine, target=5)
    s = run["stats"]
    assert s["rounds"] >= 2 and s["leads_new"] >= 5 and run["state"] == "succeeded"
    assert len(asked) > 2 and len({q.lower() for q in asked}) == len(asked)  # never repeats a query
    assert any(m["message"].startswith("Round 2:") and "of 5 leads so far" in m["message"] for m in pipeline.run_log(run_id, limit=500, engine=engine))


def test_does_not_widen_when_the_target_is_already_met_or_disabled(widen):
    engine, asked = widen
    _, run = go(engine, target=2)
    assert run["stats"]["rounds"] == 1 and len(asked) == 2
    _, run = go(engine, target=0)
    assert run["stats"]["rounds"] == 1


def test_widening_never_crawls_a_site_twice_and_respects_the_site_cap(widen):
    engine, asked = widen
    run_id, run = go(engine, target=500, max_domains=5)
    doms = [d["domain"] for d in pipeline.run_domains(run_id, engine)]
    assert len(doms) == len(set(doms)) <= 5 and run["stats"]["domains_total"] == len(doms)


def test_widening_stops_when_there_is_no_new_wording(widen, monkeypatch):
    engine, asked = widen
    monkeypatch.setattr(pipeline.planner, "more_queries", lambda plan, used, n=6: [])
    run_id, run = go(engine, target=500)
    assert run["stats"]["rounds"] == 1 and run["state"] == "succeeded"
    assert any("No more search wording" in m["message"] for m in pipeline.run_log(run_id, limit=500, engine=engine))


def test_widening_stops_after_the_time_budget_and_is_capped_at_four_rounds(widen, monkeypatch):
    engine, asked = widen
    _, run = go(engine, target=500)
    assert run["stats"]["rounds"] <= pipeline.MAX_ROUNDS
    monkeypatch.setattr(pipeline, "WIDEN_UNTIL_S", -1)
    _, run = go(engine, target=500)
    assert run["stats"]["rounds"] == 1


def test_more_queries_are_fresh_varied_and_exhaust_cleanly():
    from backend.core import planner as pl

    plan = Plan(mode="internship", industries=["AI", "fintech"], regions=["Pune", "Delhi"], search_queries=["a"])
    used: set[str] = set()
    seen = []
    while True:
        batch = pl.more_queries(plan, used, n=6)
        if not batch:
            break
        assert not (set(q.lower() for q in batch) & {u.lower() for u in used})
        used |= set(batch)
        seen += batch
    assert len(seen) == len(set(q.lower() for q in seen)) > 20
    assert any("fintech" in q for q in seen) and any("Delhi" in q for q in seen)
    assert pl.more_queries(plan, used) == []


def test_leads_from_a_run_are_scored_traced_and_disposable_domains_dropped(world, monkeypatch):
    world.web.routes["yopmail.com/"] = '<a href="mailto:boss@yopmail.com">b</a>'
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url=u, query=q) for u in HITS + ["https://yopmail.com/"]])
    run_id, _ = start(world.engine)
    got = by_email(world.engine, run_id)
    p = got["priya@acme.ai"]
    assert p["score"] > got["rahul@acme.ai"]["score"] > got["careers@acme.ai"]["score"] > 0  # a guess at the wanted CTO outranks a generic mailbox; the reasons say why
    detail = leads.get_lead(p["id"], world.engine)
    assert {r["label"] for r in detail["score_reasons"]} >= {"confidence", "name", "role", "page", "mx"}
    kinds = [t["kind"] for t in detail["trace"]]
    assert "mx" in kinds and kinds[-1] == "score" and detail["sources"][0]["page_type"] == "team" and detail["sources"][0]["page_tier"] == "http"
    assert "boss@yopmail.com" not in got
    assert any("disposable-mailbox provider" in m["message"] for m in pipeline.run_log(run_id, engine=world.engine))
