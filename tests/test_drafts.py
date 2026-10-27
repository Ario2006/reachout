"""Draft generation job, review queue API, approval rules."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.main import create_app
from backend.core import db, drafts, events, jobs, services
from backend.core.personaliser import OpenerOut
from tests import factories as f

TOKEN = "t" * 32
GOOD = OpenerOut(opener="I noticed Co builds developer tools for teams.")
BAD = OpenerOut(opener="Co is game-changing for 900 teams.")


@pytest.fixture
def client(engine, memory_keyring):
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=engine)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


@pytest.fixture(autouse=True)
def fake_services():
    fk = services.fakes(GOOD)
    services.use(fk)
    yield fk
    services.use(None)


@pytest.fixture
def camp(engine):
    db.save_settings(db.load_settings(engine).model_copy(update={"display_name": "Sam Ng"}), engine)
    return f.campaign(engine, template_id=f.template(engine))


def leads(engine, n, **kw):
    return [f.lead(engine, company="Co", **kw) for _ in range(n)]


def run(engine, cid, **payload):
    job = jobs.create_job("drafts_generate", {"campaign_id": cid, **payload}, engine)
    jobs.run_job(jobs.claim_next(engine), {"drafts_generate": drafts.run_generate_drafts}, engine)
    return jobs.get_job(job["id"], engine)


def rows(engine, cid):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text("SELECT * FROM drafts WHERE campaign_id=:c ORDER BY id"), {"c": cid})]


def lead_status(engine, lid):
    with engine.connect() as c:
        return c.execute(text("SELECT status FROM leads WHERE id=:i"), {"i": lid}).scalar()


# --------------------------------------------------------------------------- generation job
def test_generate_writes_drafts_without_footer_and_moves_leads(engine, camp, fake_services):
    ls = leads(engine, 3)
    since = events.latest_id(engine)
    job = run(engine, camp)
    assert job["state"] == "succeeded" and job["result"] == {"generated": 3, "needs_review": 0, "skipped": 0}
    assert (job["progress"], job["total"]) == (3, 3) and "3 drafts written" in job["message"]
    ds = rows(engine, camp)
    assert len(ds) == 3 and len(fake_services.fake_llm.calls) == 3
    for d in ds:
        assert d["status"] == "pending" and d["opener_source"] == "llm" and d["opener"] in d["body"]
        assert "reply 'stop'" not in d["body"] and "-- \n" not in d["body"]  # footer is added at send time
    assert {lead_status(engine, i) for i in ls} == {"drafted"}
    evs = [e for e in events.read_since(since, engine=engine) if e["type"] == "draft"]
    assert [e["payload"]["action"] for e in evs] == ["generated"] * 3 + ["bulk"]


def test_flags_and_review_status(engine, camp, fake_services):
    guessed = f.lead(engine, company="Co", email_confidence="guessed")
    nocx = f.lead(engine, company="Co", context_snippet=None)
    bad = f.lead(engine, company="Co", name=None)
    fake_services.fake_llm.responses = [GOOD, BAD]  # last one repeats
    # order of leads by id: guessed (GOOD), nocx (no call), bad (BAD, BAD)
    run(engine, camp)
    by = {d["lead_id"]: d for d in rows(engine, camp)}
    code = lambda lid: {x["code"] for x in json.loads(by[lid]["flags_json"])}  # noqa: E731
    assert code(guessed) == {"guessed_email"} and by[guessed]["status"] == "pending"
    assert code(nocx) == {"no_context"} and by[nocx]["opener_source"] == "fallback" and by[nocx]["status"] == "pending"
    assert {"opener_fallback", "banned_phrase", "unsupported_fact"} <= code(bad)
    assert by[bad]["status"] == "needs_review" and by[bad]["opener_source"] == "fallback"
    assert "Hi there," in by[bad]["body"]  # no name -> neutral greeting
    assert len(fake_services.fake_llm.calls) == 3  # good, bad+retry; none for the lead without context


def test_skips_suppressed_blocked_and_existing_and_regenerate_keeps_approved(engine, camp):
    ok, supp, replied, sent, existing = leads(engine, 5)
    with engine.begin() as c:
        c.execute(text("UPDATE leads SET status='replied' WHERE id=:i"), {"i": replied})
        c.execute(text("UPDATE leads SET status='sent' WHERE id=:i"), {"i": sent})
    with engine.connect() as c:
        supp_email = c.execute(text("SELECT email FROM leads WHERE id=:i"), {"i": supp}).scalar()
    db.add_suppression(supp_email, "manual", engine)
    f.draft(engine, camp, existing)
    job = run(engine, camp)
    assert job["result"]["generated"] == 1 and job["result"]["skipped"] == 3
    assert {d["lead_id"] for d in rows(engine, camp)} == {ok, existing}

    # regenerate: replaces the unapproved draft, never touches an approved one
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1, subject='Keep me', body='kept' WHERE lead_id=:i"), {"i": ok})
        approved = c.execute(text("SELECT id FROM drafts WHERE lead_id=:i"), {"i": ok}).scalar()
    job = run(engine, camp, regenerate=True, lead_ids=[ok, existing])
    assert job["result"]["generated"] == 1 and job["result"]["skipped"] == 1
    assert [d for d in rows(engine, camp) if d["id"] == approved][0]["body"] == "kept"


def test_cancel_mid_run_keeps_written_drafts(engine, camp, fake_services):
    leads(engine, 4)

    def cancel_after_first(schema, system, user):
        with engine.begin() as c:
            c.execute(text("UPDATE jobs SET cancel_requested=1 WHERE kind='drafts_generate'"))
        return GOOD

    fake_services.fake_llm.responses = [cancel_after_first]
    job = run(engine, camp)
    assert job["state"] == "cancelled" and len(rows(engine, camp)) == 1


def test_job_without_template_fails_with_message(engine):
    cid = f.campaign(engine)
    job = run(engine, cid)
    assert job["state"] == "failed" and "template" in job["error"]


def test_generate_endpoint_validates_and_dedupes(client, engine, camp):
    assert client.post("/drafts/generate", json={"campaign_id": 999}).status_code == 404
    assert client.post("/drafts/generate", json={"campaign_id": f.campaign(engine)}).status_code == 422
    a = client.post("/drafts/generate", json={"campaign_id": camp})
    b = client.post("/drafts/generate", json={"campaign_id": camp})
    assert a.status_code == 202 and a.json()["kind"] == "drafts_generate" and b.json()["id"] == a.json()["id"]


# --------------------------------------------------------------------------- review API
def make(client, engine, camp, n=3):
    leads(engine, n)
    run(engine, camp)
    return client.get("/drafts", params={"campaign_id": camp}).json()


def test_list_filters_counts_and_detail(client, engine, camp, fake_services):
    page = make(client, engine, camp, 3)
    assert page["total"] == 3 and page["counts"] == {"pending": 3, "needs_review": 0, "approved": 0, "rejected": 0}
    d = page["items"][0]
    assert d["opener_span"] and d["body"][d["opener_span"]["start"]:d["opener_span"]["end"]] == d["opener"]
    det = client.get(f"/drafts/{d['id']}").json()
    assert det["final_text"].startswith(det["body"]) and "-- \nSam Ng\n" in det["final_text"] and "reply 'stop'" in det["final_text"]
    assert client.get("/drafts/999").status_code == 404
    client.patch(f"/drafts/{d['id']}", json={"status": "rejected"})
    r = client.get("/drafts", params={"campaign_id": camp, "status": ["pending", "approved"]}).json()
    assert r["total"] == 2 and r["counts"]["rejected"] == 1
    assert client.get("/drafts", params={"campaign_id": camp, "flagged": True}).json()["total"] == 0
    assert client.get("/drafts", params={"campaign_id": camp, "q": "nomatch"}).json()["total"] == 0
    assert client.get("/drafts", params={"campaign_id": camp, "q": d["lead"]["email"][:8]}).json()["total"] >= 1
    assert client.get("/drafts", params={"campaign_id": camp, "limit": 1, "offset": 1}).json()["items"][0]["id"] != d["id"]


def test_editing_an_approved_draft_withdraws_approval(client, engine, camp):
    d = make(client, engine, camp, 1)["items"][0]
    ok = client.patch(f"/drafts/{d['id']}", json={"status": "approved"}).json()
    assert ok["status"] == "approved" and lead_status(engine, d["lead"]["id"]) == "approved"
    edited = client.patch(f"/drafts/{d['id']}", json={"body": ok["body"] + "\n\nP.S. Edited."}).json()
    assert edited["status"] == "pending" and "P.S. Edited." in edited["final_text"]
    assert "edited" in [x["code"] for x in edited["flags"]]
    row = rows(engine, camp)[0]
    assert (row["status"], row["approved"], row["approved_at"]) == ("pending", 0, None)
    assert lead_status(engine, d["lead"]["id"]) == "drafted"
    # editing the subject alone also withdraws; an identical resend does not
    client.patch(f"/drafts/{d['id']}", json={"status": "approved"})
    assert client.patch(f"/drafts/{d['id']}", json={"subject": edited["subject"], "body": edited["body"]}).json()["status"] == "approved"
    assert client.patch(f"/drafts/{d['id']}", json={"subject": "New subject"}).json()["status"] == "pending"


def test_edit_and_approve_in_one_request_approves_the_new_text(client, engine, camp):
    d = make(client, engine, camp, 1)["items"][0]
    r = client.patch(f"/drafts/{d['id']}", json={"body": d["body"] + " Thanks.", "status": "approved"}).json()
    assert r["status"] == "approved" and r["body"].endswith("Thanks.")


def test_edit_that_removes_the_opener_marks_it_manual(client, engine, camp):
    d = make(client, engine, camp, 1)["items"][0]
    r = client.patch(f"/drafts/{d['id']}", json={"body": "Totally rewritten."}).json()
    assert r["opener_source"] == "manual" and r["opener"] is None and r["opener_span"] is None


def test_patch_rules(client, engine, camp, fake_services):
    ls = leads(engine, 2)
    fake_services.fake_llm.responses = [BAD]
    run(engine, camp, lead_ids=[ls[0]])
    nr = client.get("/drafts", params={"campaign_id": camp}).json()["items"][0]
    assert nr["status"] == "needs_review"
    assert client.patch(f"/drafts/{nr['id']}", json={"status": "pending"}).status_code == 409  # review is not clearable
    assert client.patch(f"/drafts/{nr['id']}", json={"status": "approved"}).json()["status"] == "approved"  # explicit approve is
    assert client.patch(f"/drafts/{nr['id']}", json={"status": "needs_review"}).status_code == 422
    assert client.patch("/drafts/999", json={"status": "approved"}).status_code == 404
    # a suppressed address cannot be approved
    fake_services.fake_llm.responses = [GOOD]
    run(engine, camp, lead_ids=[ls[1]])
    d2 = [d for d in client.get("/drafts", params={"campaign_id": camp}).json()["items"] if d["lead"]["id"] == ls[1]][0]
    db.add_suppression(d2["lead"]["email"], "manual", engine)
    r = client.patch(f"/drafts/{d2['id']}", json={"status": "approved"})
    assert r.status_code == 409 and "suppression" in r.json()["detail"]


def test_bulk_approve_unflagged_only_and_never_needs_review(client, engine, camp, fake_services):
    leads(engine, 3)
    g = f.lead(engine, company="Co", email_confidence="guessed")
    nr = f.lead(engine, company="Co")
    fake_services.fake_llm.responses = [GOOD, GOOD, GOOD, GOOD, BAD]
    run(engine, camp)
    page = client.get("/drafts", params={"campaign_id": camp}).json()
    assert page["counts"]["needs_review"] == 1
    r = client.post("/drafts/bulk-approve", json={"campaign_id": camp}).json()
    assert r == {"approved": 3, "skipped_flagged": 1, "skipped_blocked": 0}
    # flagged but pending is approvable only when asked for; needs_review still is not, even when listed by id
    ids = [d["id"] for d in page["items"]]
    r = client.post("/drafts/bulk-approve", json={"campaign_id": camp, "draft_ids": ids, "only_unflagged": False}).json()
    assert r["approved"] == 1
    counts = client.get("/drafts", params={"campaign_id": camp}).json()["counts"]
    assert counts == {"pending": 0, "needs_review": 1, "approved": 4, "rejected": 0}
    assert lead_status(engine, nr) == "drafted" and lead_status(engine, g) == "approved"


def test_bulk_approve_skips_suppressed_and_blocked(client, engine, camp):
    page = make(client, engine, camp, 3)
    a, b, _ = page["items"]
    db.add_suppression(a["lead"]["email"], "manual", engine)
    with engine.begin() as c:
        c.execute(text("UPDATE leads SET status='bounced' WHERE id=:i"), {"i": b["lead"]["id"]})
    assert client.post("/drafts/bulk-approve", json={"campaign_id": camp}).json() == {"approved": 1, "skipped_flagged": 0, "skipped_blocked": 2}


def test_bulk_approve_of_50_is_one_call_and_idempotent(client, engine, camp):
    make(client, engine, camp, 50)
    assert client.post("/drafts/bulk-approve", json={"campaign_id": camp}).json()["approved"] == 50
    assert client.post("/drafts/bulk-approve", json={"campaign_id": camp}).json()["approved"] == 0


def test_regenerate_opener_keeps_hand_edits_and_withdraws_approval(client, engine, camp, fake_services):
    d = make(client, engine, camp, 1)["items"][0]
    edited = client.patch(f"/drafts/{d['id']}", json={"body": d["body"] + "\n\nP.S. hand written."}).json()
    client.patch(f"/drafts/{d['id']}", json={"status": "approved"})
    new = OpenerOut(opener="Your developer tools for teams look useful.")
    fake_services.fake_llm.responses = [new]
    r = client.post(f"/drafts/{d['id']}/regenerate-opener").json()
    assert r["opener"] == new.opener and new.opener in r["body"] and d["opener"] not in r["body"]
    assert r["body"].endswith("P.S. hand written.") and r["status"] == "pending"
    assert "Previous" not in fake_services.fake_llm.calls[-1][1] and d["opener"] in fake_services.fake_llm.calls[-1][1]
    # failing twice -> neutral opener and needs_review again
    fake_services.fake_llm.responses = [BAD]
    r = client.post(f"/drafts/{d['id']}/regenerate-opener").json()
    assert r["status"] == "needs_review" and r["opener_source"] == "fallback"
    assert {"opener_fallback", "edited"} <= {x["code"] for x in r["flags"]}
    fake_services.fake_llm.responses = [new]
    assert client.post(f"/drafts/{d['id']}/regenerate-opener").json()["status"] == "pending"  # review cleared by a good opener
    assert client.post("/drafts/999/regenerate-opener").status_code == 404
    assert edited["status"] == "pending"


def test_regenerate_after_opener_was_edited_away_rebuilds_from_template(client, engine, camp, fake_services):
    d = make(client, engine, camp, 1)["items"][0]
    client.patch(f"/drafts/{d['id']}", json={"body": "Totally rewritten."})
    fake_services.fake_llm.responses = [OpenerOut(opener="Your developer tools for teams look useful.")]
    r = client.post(f"/drafts/{d['id']}/regenerate-opener").json()
    assert r["body"].startswith("Hi ") and r["opener_source"] == "llm" and "edited" not in [x["code"] for x in r["flags"]]
