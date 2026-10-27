import json

import pytest

from backend.core import db, domains, leads, scoring
from backend.core.extractor import LeadDraft
from backend.core.models import EmailConfidence as C

NOW = 1_000_000_000.0


def draft(email="priya@acme.ai", conf=C.published_personal, name="Priya Sharma", role="CTO", ptype="team", **kw):
    return LeadDraft(email=email, name=name, role=role, company="Acme", domain="acme.ai", source_url="https://acme.ai/team",
                     context_snippet="Priya Sharma, CTO", email_confidence=conf, page_type=ptype, fetched_at=NOW, **kw)


def pts(reasons):
    return {r.label: r.points for r in reasons}


def test_a_published_personal_address_on_a_team_page_for_a_wanted_role_scores_high():
    s, r = scoring.score_lead(draft(), mode="internship", target_roles=["CTO"], now=NOW)
    assert pts(r) == {"confidence": 40, "name": 15, "role": 15, "page": 10, "mx": 10, "fresh": 5} and s == 95


def test_score_orders_the_labels_and_explains_every_point():
    personal, _ = scoring.score_lead(draft(), mode="service", target_roles=["CTO"], now=NOW)
    generic, rg = scoring.score_lead(draft("info@acme.ai", C.published_generic, "", "", "contact"), mode="service", now=NOW)
    guessed, _ = scoring.score_lead(draft("priya.sharma@acme.ai", C.guessed), mode="service", target_roles=["CTO"], now=NOW)
    assert personal > guessed > 0 and personal > generic > 0
    assert sum(x.points for x in rg) == generic and all(x.detail for x in rg)


def test_name_and_role_that_do_not_match_earn_less():
    _, r = scoring.score_lead(draft(name="Someone Else", role="Barista"), mode="service", target_roles=["CTO"], now=NOW)
    assert pts(r)["name"] == 5 and pts(r)["role"] == 6


def test_careers_pages_count_more_for_job_modes_and_stale_pages_less():
    _, job = scoring.score_lead(draft(ptype="careers"), mode="internship", now=NOW)
    _, svc = scoring.score_lead(draft(ptype="careers"), mode="service", now=NOW)
    assert pts(job)["page"] == 8 and pts(svc)["page"] == 4
    _, week = scoring.score_lead(draft(), mode="service", now=NOW + 5 * 86400)
    _, old = scoring.score_lead(draft(), mode="service", now=NOW + 60 * 86400)
    assert pts(week)["fresh"] == 3 and "fresh" not in pts(old)


def test_no_mx_loses_the_mx_points_and_the_score_is_clamped():
    _, r = scoring.score_lead(draft(), mode="service", mx_ok=False, now=NOW)
    assert "mx" not in pts(r)
    s, _ = scoring.score_lead(draft(), mode="service", target_roles=["CTO"], now=NOW)
    assert 0 <= s <= 100


def test_disposable_domains_are_recognised():
    assert domains.is_disposable("mailinator.com") and domains.is_disposable("x.mailinator.com") and domains.is_disposable("YOPMAIL.com")
    assert not domains.is_disposable("acme.ai")


# ---- ingest: score stored, dedupe by email and by person, sources merged, trace kept -------------------------

def _row(engine, email):
    return leads.query_leads(leads.LeadFilter(q=email), engine=engine)[0][0]


def test_ingest_stores_score_reasons_sources_and_a_trace(engine):
    d = draft(trace=[("found", "Found on the team page")], also=[{"url": "https://acme.ai/about", "type": "about", "tier": "http", "fetched_at": NOW, "snippet": "x"}])
    d.mx_ok = True
    res = leads.ingest([d], run_id=None, mode="internship", target_roles=["CTO"], engine=engine)
    assert res.new[0]["score"] >= 80
    got = leads.get_lead(res.new[0]["id"], engine)
    assert got["score"] == res.new[0]["score"] and {r["label"] for r in got["score_reasons"]} >= {"confidence", "name", "role"}
    assert [s["source_url"] for s in got["sources"]] == ["https://acme.ai/team", "https://acme.ai/about"]
    kinds = [t["kind"] for t in got["trace"]]
    assert kinds[0] == "found" and kinds[-1] == "score" and "Scored" in got["trace"][-1]["message"]


def test_the_same_email_is_not_stored_twice_but_its_new_source_is_kept(engine):
    leads.ingest([draft()], run_id=None, mode="service", engine=engine)
    d2 = draft(); d2.source_url = "https://acme.ai/contact"
    res = leads.ingest([d2], run_id=None, mode="service", engine=engine)
    assert res.duplicates == 1 and not res.new
    lid = _row(engine, "priya@acme.ai")["id"]
    assert [s["source_url"] for s in leads.get_lead(lid, engine)["sources"]] == ["https://acme.ai/team", "https://acme.ai/contact"]


def test_a_published_address_replaces_a_guess_for_the_same_person(engine):
    leads.ingest([draft("priya.sharma@acme.ai", C.guessed)], run_id=None, mode="service", engine=engine)
    low = _row(engine, "priya.sharma@acme.ai")
    res = leads.ingest([draft("priya@acme.ai")], run_id=None, mode="service", engine=engine)
    assert res.upgraded == 1 and not res.new
    rows = leads.query_leads(leads.LeadFilter(), engine=engine)[0]
    assert len(rows) == 1 and rows[0]["email"] == "priya@acme.ai" and rows[0]["email_confidence"] == "published_personal"
    assert rows[0]["id"] == low["id"] and rows[0]["score"] > low["score"]
    assert "Replaced priya.sharma@acme.ai" in leads.get_lead(rows[0]["id"], engine)["trace"][-2]["message"]


def test_a_guess_never_replaces_or_duplicates_a_published_address_for_the_same_person(engine):
    leads.ingest([draft("priya@acme.ai")], run_id=None, mode="service", engine=engine)
    res = leads.ingest([draft("priya.sharma@acme.ai", C.guessed)], run_id=None, mode="service", engine=engine)
    assert res.duplicates == 1 and not res.new
    assert [r["email"] for r in leads.query_leads(leads.LeadFilter(), engine=engine)[0]] == ["priya@acme.ai"]


def test_same_name_at_a_different_company_is_a_different_person(engine):
    other = draft("priya@beta.io"); other.domain = "beta.io"
    res = leads.ingest([draft(), other], run_id=None, mode="service", engine=engine)
    assert len(res.new) == 2


def test_min_score_filter_and_sort_by_score(engine):
    leads.ingest([draft(), draft("info@acme.ai", C.published_generic, "", "", "other"), draft("sam.lee@acme.ai", C.guessed, "Sam Lee", "")], run_id=None, mode="service", engine=engine)
    rows, total = leads.query_leads(leads.LeadFilter(), sort="score:desc", engine=engine)
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True) and total == 3
    hi, n = leads.query_leads(leads.LeadFilter(min_score=60), engine=engine)
    assert n == 1 and hi[0]["email"] == "priya@acme.ai"


def test_suppressed_addresses_are_still_never_ingested(engine):
    db.add_suppression("priya@acme.ai", "opt_out", engine)
    res = leads.ingest([draft()], run_id=None, mode="service", engine=engine)
    assert res.suppressed == 1 and not res.new and leads.query_leads(leads.LeadFilter(), engine=engine)[1] == 0


def test_the_pipeline_log_has_one_json_line_per_decision_with_the_trace_id(engine, home):
    from backend.core import config, pipeline
    from backend.core.planner import Plan
    run = pipeline.create_run("x", Plan(mode="service", search_queries=["q"]), pipeline.RunOptions(), engine)
    leads.ingest([draft()], run_id=run["id"], mode="service", engine=engine)
    from backend.core import config
    lines = [json.loads(x) for x in (config.logs_dir() / "pipeline.jsonl").read_text().splitlines()]
    assert lines[-1]["kind"] == "lead.created" and lines[-1]["run_id"] == run["id"] and lines[-1]["trace_id"] and lines[-1]["score"] > 0
    assert "priya" not in json.dumps(lines)  # the log carries ids and domains, not addresses
