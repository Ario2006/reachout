import httpx
import pytest
from sqlalchemy import text

from backend.core import config, crawler, db, discovery, events, extractor, jobs, leads, llm, pipeline, validator
from backend.core.job_handlers import HANDLERS
from backend.core.models import AppSettings
from backend.core.planner import Plan

REAL_FETCHER = crawler.Fetcher  # captured before any patching
REAL_MX = validator.MxChecker
TEAM = """<html><head><title>Acme AI | Team</title></head><body>
<p>Priya Sharma, Co-founder &amp; CEO <a href="mailto:priya@acme.ai">priya@acme.ai</a></p>
<p>Rahul Verma, CTO</p><p>Contact: careers@acme.ai</p></body></html>"""
WEB = {
    "https://acme.ai/": '<title>Acme AI | Home</title><a href="/team">Team</a>', "https://acme.ai/team": TEAM,
    "https://only-info.com/": '<a href="mailto:hello@only-info.com">hi</a>',
    "https://nomx.ai/": '<a href="mailto:hi@nomx.ai">hi</a>',
    "https://robots-no.com/robots.txt": "User-agent: *\nDisallow: /",
    "https://robots-no.com/": "<a href='mailto:x@robots-no.com'>x</a>",
    "https://suppressed.io/": '<a href="mailto:ceo@suppressed.io">x</a><a href="mailto:info@suppressed.io">y</a>',
}
HITS = ["https://acme.ai/about", "https://only-info.com/", "https://nomx.ai/", "https://robots-no.com/", "https://dead.com/",
        "https://suppressed.io/", "https://linkedin.com/in/x", "https://acme.ai/team"]


def mock_client():
    def handler(req):
        r = WEB.get(str(req.url))
        return httpx.Response(200, text=r, headers={"content-type": "text/html"}) if r else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.fixture
def patched(monkeypatch, engine):
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url=u, query=q) for u in HITS])
    monkeypatch.setattr(pipeline.crawler, "Fetcher", lambda **kw: REAL_FETCHER(client=mock_client(), delay_s=(0, 0), check_public=False))
    monkeypatch.setattr(pipeline.validator, "MxChecker", lambda: REAL_MX(lookup=lambda d: d != "nomx.ai"))
    monkeypatch.setattr(llm, "is_running", lambda *a, **k: True)
    monkeypatch.setattr(extractor, "llm_people_fn", lambda s: lambda text_, dom: [
        extractor.Person(name="Priya Sharma", role="Co-founder & CEO"), extractor.Person(name="Rahul Verma", role="CTO"),
        extractor.Person(name="Ghost Writer", role="CTO")])
    db.save_settings(AppSettings(crawl_concurrency=2, target_leads=0), engine)  # single round unless a test widens
    return engine


def start(engine, recrawl=False, queries=("q1", "q2"), roles=("CTO",)):
    plan = Plan(mode="internship", target_roles=list(roles), search_queries=list(queries))
    run = pipeline.create_run("AI internships in Bangalore", plan, pipeline.RunOptions(recrawl=recrawl), engine)
    job = jobs.get_job(run["job_id"], engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    return run["id"], jobs.get_job(job["id"], engine)


def by_email(engine, run_id=None):
    rows = leads.query_leads(leads.LeadFilter(run_id=run_id), engine=engine)[0]
    return {r["email"]: r for r in rows}


def test_end_to_end_run(patched):
    engine = patched
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
    # a CTO listed with no published email gets a guess (pattern inferred: first@); invented names never appear
    assert got["rahul@acme.ai"]["email_confidence"] == "guessed" or got["rahul@acme.ai"]["email_confidence"] == "published_personal"
    assert not any("ghost" in e for e in got)
    # dropped: no MX, robots-disallowed, 404 site, suppressed, linkedin
    for absent in ("hi@nomx.ai", "x@robots-no.com", "ceo@suppressed.io"):
        assert absent not in got


def test_run_record_domains_log_events_and_csv(patched):
    engine = patched
    run_id, _ = start(engine)
    run = pipeline.get_run(run_id, engine)
    assert run["state"] == "succeeded" and run["stage"] == "done" and run["finished_at"]
    s = run["stats"]
    assert s["queries_total"] == 2 and s["queries_done"] == 2 and s["search_hits"] == 2 * len(HITS)
    assert s["domains_total"] == 6 and s["domains_done"] == 6 and s["leads_new"] == run["leads_found"]
    assert s["leads_dropped"] >= 1 and s["pages"] >= 5 and s["llm_calls"] >= 1
    assert sum(s["by_confidence"].values()) == s["leads_new"]

    states = {d["domain"]: d["state"] for d in pipeline.run_domains(run_id, engine)}
    assert states["acme.ai"] == "done" and states["only-info.com"] == "done" and states["robots-no.com"] == "failed" and states["dead.com"] == "failed"
    assert "linkedin.com" not in states  # blocklist

    msgs = [r["message"] for r in pipeline.run_log(run_id, engine=engine)]
    assert any("Searched" in m for m in msgs) and any("acme.ai" in m for m in msgs) and msgs[-1].startswith("Finished")

    evs = events.read_since(0, 5000, engine)
    kinds = {e["type"] for e in evs}
    assert {"run", "run_domain", "run_log", "lead", "job"} <= kinds
    last_run = [e for e in evs if e["type"] == "run"][-1]["payload"]
    assert last_run["state"] == "succeeded" and last_run["run_id"] == run_id

    csv = (config.exports_dir() / f"leads-run-{run_id}.csv").read_text()
    assert csv.splitlines()[0].startswith("name,email") and "priya@acme.ai" in csv


def test_rerun_skips_recently_crawled_domains_unless_recrawl(patched):
    engine = patched
    start(engine)
    run2, job2 = start(engine)
    r2 = pipeline.get_run(run2, engine)
    assert r2["stats"]["domains_total"] == 2  # only the two failed sites are retried; "done" ones are skipped
    run3, _ = start(engine, recrawl=True)
    assert pipeline.get_run(run3, engine)["stats"]["domains_total"] == 6
    assert pipeline.get_run(run3, engine)["stats"]["leads_new"] == 0  # all already known
    assert pipeline.get_run(run3, engine)["stats"]["leads_dup"] >= 4


def test_max_domains_limit(patched):
    engine = patched
    plan = Plan(mode="service", search_queries=["q"])
    run = pipeline.create_run("x", plan, pipeline.RunOptions(max_domains=2), engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    assert pipeline.get_run(run["id"], engine)["stats"]["domains_total"] == 2


def test_cancel_keeps_partial_results_and_marks_cancelled(patched, monkeypatch):
    engine = patched
    class CancellingFetcher(REAL_FETCHER):
        n = 0

        def get(self, url):
            CancellingFetcher.n += 1
            if CancellingFetcher.n == 3:
                jobs.request_cancel(JOB["id"], engine)
            return super().get(url)

    monkeypatch.setattr(pipeline.crawler, "Fetcher", lambda **kw: CancellingFetcher(client=mock_client(), delay_s=(0, 0), check_public=False))
    monkeypatch.setattr(pipeline, "_Cancel", type("C", (), {"__init__": lambda s, ctx: setattr(s, "ctx", ctx), "__call__": lambda s: s.ctx.cancelled()}))
    plan = Plan(mode="internship", target_roles=["CTO"], search_queries=["q1"])
    run = pipeline.create_run("x", plan, pipeline.RunOptions(), engine)
    JOB = jobs.get_job(run["job_id"], engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    assert jobs.get_job(JOB["id"], engine)["state"] == "cancelled"
    r = pipeline.get_run(run["id"], engine)
    assert r["state"] == "cancelled" and r["finished_at"]
    assert r["stats"]["domains_done"] < 6
    assert "Cancelled" in pipeline.run_log(run["id"], engine=engine)[-1]["message"]


def test_emails_only_when_ollama_is_down(patched, monkeypatch):
    engine = patched
    monkeypatch.setattr(llm, "is_running", lambda *a, **k: False)
    run_id, job = start(engine)
    got = by_email(engine, run_id)
    assert job["state"] == "succeeded" and got["priya@acme.ai"]["name"] == ""  # no model to vouch for a first-name address
    assert got["priya@acme.ai"]["email_confidence"] == "published_generic"
    assert got["priya@acme.ai"]["role"] in ("", None)
    assert not any(r["email_confidence"] == "guessed" for r in got.values())
    assert any("Ollama is not running" in m["message"] for m in pipeline.run_log(run_id, engine=engine))


def test_model_failure_mid_run_degrades_gracefully(patched, monkeypatch):
    engine = patched

    def broken(settings):
        def fn(t, d):
            raise llm.LlmError("model crashed")
        return fn

    monkeypatch.setattr(extractor, "llm_people_fn", broken)
    run_id, job = start(engine)
    assert job["state"] == "succeeded" and "priya@acme.ai" in by_email(engine, run_id)
    assert any("stopped responding" in m["message"] for m in pipeline.run_log(run_id, engine=engine))


def test_one_crashing_site_does_not_stop_the_run(patched, monkeypatch):
    engine = patched
    real = pipeline._process_site

    def flaky(run, cand, *a, **k):
        if cand.domain == "only-info.com":
            raise RuntimeError("boom")
        return real(run, cand, *a, **k)

    monkeypatch.setattr(pipeline, "_process_site", flaky)
    run_id, job = start(engine)
    st = {d["domain"]: d for d in pipeline.run_domains(run_id, engine)}
    assert job["state"] == "succeeded" and st["only-info.com"]["state"] == "failed" and "boom" in st["only-info.com"]["note"]
    assert "priya@acme.ai" in by_email(engine, run_id)


def test_suppressed_addresses_never_become_leads(patched):
    engine = patched
    db.add_suppression("info@suppressed.io", "bounce", engine)
    db.add_suppression("priya@acme.ai", "opt_out", engine)
    run_id, _ = start(engine)
    got = by_email(engine, run_id)
    assert "info@suppressed.io" not in got and "priya@acme.ai" not in got
    assert pipeline.get_run(run_id, engine)["stats"]["leads_suppressed"] >= 2


def test_progress_events_reach_the_job(patched):
    engine = patched
    _, job = start(engine)
    msgs = [e["payload"]["message"] for e in events.read_since(0, 5000, engine) if e["type"] == "job" and e["payload"]["kind"] == "discovery_run"]
    assert any(m and m.startswith("Searching") for m in msgs) and any(m and m.startswith("Crawling") for m in msgs)
    assert job["progress"] == job["total"] == 6


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


def test_a_site_contributes_only_its_best_addresses(patched, monkeypatch):
    engine = patched
    many = "".join(f'<a href="mailto:office{i}@big.com">o</a>' for i in range(20)) + '<a href="mailto:careers@big.com">c</a><a href="mailto:jobs@big.com">j</a>'
    WEB["https://big.com/"] = many
    monkeypatch.setattr(discovery, "make_searcher", lambda s, p: lambda q: [discovery.SearchHit(url="https://big.com/", query=q)])
    db.save_settings(AppSettings(crawl_concurrency=1, max_leads_per_site=4, target_leads=0), engine)
    try:
        run_id, _ = start(engine)
    finally:
        del WEB["https://big.com/"]
    got = by_email(engine, run_id)
    assert len(got) == 4 and {"careers@big.com", "jobs@big.com"} <= set(got)
    s = pipeline.get_run(run_id, engine)["stats"]
    assert s["leads_trimmed"] == 18 and s["leads_new"] == 4
    assert any("kept the best 4 of 22" in m["message"] for m in pipeline.run_log(run_id, engine=engine))


# ---- widening rounds ---------------------------------------------------------------------------

def widen_web(n_sites=12):
    """Sites s0..s{n-1}, each with one distinct generic address. Round-1 queries only surface the first two."""
    for i in range(n_sites):
        WEB[f"https://s{i}.com/"] = f'<title>Site {i}</title><a href="mailto:hello@s{i}.com">hi</a>'
    return [f"s{i}.com" for i in range(n_sites)]


@pytest.fixture
def widen(patched, monkeypatch):
    engine = patched
    widen_web()
    asked: list[str] = []

    def searcher_for(settings, plan):
        def search(q):
            asked.append(q)
            n = 2 if q in ("first query", "second query") else 4
            start = 0 if n == 2 else (len(asked) - 2) * 2  # later queries surface different sites
            return [discovery.SearchHit(url=f"https://s{(start + i) % 12}.com/", query=q) for i in range(n)]
        return search

    monkeypatch.setattr(discovery, "make_searcher", searcher_for)
    yield engine, asked
    for i in range(12):
        WEB.pop(f"https://s{i}.com/", None)


def go(engine, target, **opt):
    db.save_settings(AppSettings(crawl_concurrency=1, target_leads=target, max_leads_per_site=5), engine)
    plan = Plan(mode="service", industries=["ai"], regions=["pune"], search_queries=["first query", "second query"])
    run = pipeline.create_run("x", plan, pipeline.RunOptions(expand_lists=False, **opt), engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    return run["id"], pipeline.get_run(run["id"], engine)


def test_widens_with_new_wording_until_the_target_is_met(widen):
    engine, asked = widen
    run_id, run = go(engine, target=5)
    s = run["stats"]
    assert s["rounds"] >= 2 and s["leads_new"] >= 5 and run["state"] == "succeeded"
    assert len(asked) > 2 and len(set(q.lower() for q in asked)) == len(asked)  # never repeats a query
    log = [m["message"] for m in pipeline.run_log(run_id, limit=500, engine=engine)]
    assert any(m.startswith("Round 2:") and "of 5 leads so far" in m for m in log)


def test_does_not_widen_when_the_target_is_already_met_or_disabled(widen):
    engine, asked = widen
    _, run = go(engine, target=2)
    assert run["stats"]["rounds"] == 1 and len(asked) == 2
    asked.clear()
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


def test_widening_stops_after_the_time_budget(widen, monkeypatch):
    engine, asked = widen
    monkeypatch.setattr(pipeline, "WIDEN_UNTIL_S", -1)
    _, run = go(engine, target=500)
    assert run["stats"]["rounds"] == 1


def test_widening_is_capped_at_four_rounds(widen):
    engine, asked = widen
    _, run = go(engine, target=500)
    assert run["stats"]["rounds"] <= pipeline.MAX_ROUNDS


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
