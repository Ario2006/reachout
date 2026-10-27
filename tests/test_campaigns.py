import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.main import create_app
from backend.core import campaigns, jobs, secrets, services
from backend.core.job_handlers import HANDLERS
from backend.core.ports import RecipientRejected
from tests.sending_world import ME, camp, fresh, make_campaign, msg, sender

TOKEN = "t" * 32


@pytest.fixture
def world(engine, memory_keyring):
    svc, s = fresh(engine)
    secrets.set_app_password(ME, "abcdefghijklmnop")
    yield engine, svc
    services.use(None)


@pytest.fixture
def client(world):
    engine, _ = world
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=engine)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


def draft_campaign(engine, n=2, **kw):
    return make_campaign(engine, n, state="draft", approved=False, **kw)


def approve_all(engine, cid):
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE campaign_id=:c"), {"c": cid})


def test_readiness_lists_blockers_with_reasons(world, client):
    engine, _ = world
    cid, _ = draft_campaign(engine)
    r = client.get(f"/campaigns/{cid}/readiness").json()
    items = {i["id"]: i for i in r["items"]}
    assert r["ready"] is False and items["approved_drafts"]["ok"] is False and items["gmail"]["ok"] is True
    assert items["test_sent"]["blocking"] is False
    approve_all(engine, cid)
    assert client.get(f"/campaigns/{cid}/readiness").json()["ready"] is True
    assert client.get("/campaigns/999/readiness").status_code == 404


def test_readiness_gmail_and_attachment(world, client, tmp_path):
    engine, _ = world
    cid, _ = draft_campaign(engine)
    approve_all(engine, cid)
    secrets.delete_app_password(ME)
    r = client.get(f"/campaigns/{cid}/readiness").json()
    assert r["ready"] is False and "App Password" in [i for i in r["items"] if i["id"] == "gmail"][0]["detail"]
    secrets.set_app_password(ME, "abcdefghijklmnop")
    big = tmp_path / "cv.pdf"
    big.write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET attachment_path=:p WHERE id=:i"), {"p": str(big), "i": cid})
    r = client.get(f"/campaigns/{cid}/readiness").json()
    att = [i for i in r["items"] if i["id"] == "attachment"][0]
    assert att["ok"] is False and att["blocking"] is False and r["ready"] is True  # large: warn, do not block
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET attachment_path='/nope/x.pdf' WHERE id=:i"), {"i": cid})
    r = client.get(f"/campaigns/{cid}/readiness").json()
    assert r["ready"] is False


def test_start_refuses_without_gmail_or_approved_drafts(world, client):
    engine, _ = world
    cid, _ = draft_campaign(engine)
    r = client.post(f"/campaigns/{cid}/start")
    assert r.status_code == 409 and "approve" in r.json()["detail"].lower()
    approve_all(engine, cid)
    secrets.delete_app_password(ME)
    r = client.post(f"/campaigns/{cid}/start")
    assert r.status_code == 409 and "Gmail" in r.json()["detail"]
    assert camp(engine, cid)["state"] == "draft"
    assert client.post("/campaigns/999/start").status_code == 404


def test_start_enqueues_only_approved_drafts_with_a_snapshot(world, client):
    engine, svc = world
    cid, leads = draft_campaign(engine, 3)
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE lead_id IN (:a, :b)"), {"a": leads[0], "b": leads[1]})
    out = client.post(f"/campaigns/{cid}/start").json()
    assert out["state"] == "running" and out["started_at"] and out["counts"]["queued"] == 2
    m = msg(engine, cid, leads[0])
    assert m["body"] == "Hi 0,\n\nI like your work." and m["subject"] == "Hello 0" and m["draft_id"]
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM messages WHERE lead_id=:i"), {"i": leads[2]}).scalar() == 0
        assert c.execute(text("SELECT status FROM leads WHERE id=:i"), {"i": leads[0]}).scalar() == "queued"
    assert client.post(f"/campaigns/{cid}/start").status_code == 409  # already running


def test_start_refuses_when_paused_all(world, client):
    engine, _ = world
    cid, _ = draft_campaign(engine)
    approve_all(engine, cid)
    client.post("/sending/pause-all")
    assert client.post(f"/campaigns/{cid}/start").status_code == 409


def test_pause_resume_and_locking(world, client):
    engine, svc = world
    cid, _ = draft_campaign(engine)
    approve_all(engine, cid)
    client.post(f"/campaigns/{cid}/start")
    assert client.put(f"/campaigns/{cid}", json={"name": "x"}).status_code == 409  # running: locked
    p = client.post(f"/campaigns/{cid}/pause").json()
    assert p["state"] == "paused" and p["paused_by"] == "user"
    assert client.put(f"/campaigns/{cid}", json={"name": "renamed"}).status_code == 200
    assert client.post(f"/campaigns/{cid}/pause").json()["state"] == "paused"  # idempotent
    r = client.post(f"/campaigns/{cid}/resume").json()
    assert r["state"] == "running" and r["paused_by"] is None and r["pause_reason"] is None
    assert client.post(f"/campaigns/{cid}/resume").status_code == 409  # not paused
    assert client.post("/campaigns/999/pause").status_code == 404


def test_resume_picks_up_drafts_approved_after_start(world, client):
    engine, svc = world
    cid, leads = draft_campaign(engine, 2)
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE lead_id=:a"), {"a": leads[0]})
    client.post(f"/campaigns/{cid}/start")
    client.post(f"/campaigns/{cid}/pause")
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE lead_id=:a"), {"a": leads[1]})
    client.post(f"/campaigns/{cid}/resume")
    assert client.get(f"/campaigns/{cid}/stats").json()["queued"] == 2


def test_resume_after_login_pause_needs_gmail(world, client):
    engine, svc = world
    cid, _ = make_campaign(engine, 2)
    campaigns.pause(cid, "smtp_login", "Gmail rejected the login.", engine)
    secrets.delete_app_password(ME)
    assert client.post(f"/campaigns/{cid}/resume").status_code == 409
    secrets.set_app_password(ME, "abcdefghijklmnop")
    assert client.post(f"/campaigns/{cid}/resume").json()["state"] == "running"


def test_test_send_goes_to_self_with_footer_and_is_not_recorded(world, client):
    engine, svc = world
    cid, _ = draft_campaign(engine)
    assert client.post(f"/campaigns/{cid}/test-send").status_code == 409  # nothing approved yet
    approve_all(engine, cid)
    r = client.post(f"/campaigns/{cid}/test-send").json()
    assert r["to"] == ME and r["message_id"].startswith("<")
    [e] = svc.fake_mailer.sent
    assert e.to_addr == ME and e.subject == "[Test] Hello 0" and "reply 'stop'" in e.body and e.message_id == r["message_id"]
    assert camp(engine, cid)["test_sent_at"]
    assert client.get(f"/campaigns/{cid}/messages").json() == []
    assert client.get(f"/campaigns/{cid}/readiness").json()["items"][3]["ok"] is True


def test_test_send_errors_are_409_with_a_sentence(world, client):
    engine, svc = world
    cid, _ = draft_campaign(engine)
    approve_all(engine, cid)
    svc.fake_mailer.fail_all = RecipientRejected("550 nope")
    r = client.post(f"/campaigns/{cid}/test-send")
    assert r.status_code == 409 and "not sent" in r.json()["detail"] and svc.fake_mailer.closed
    assert client.post("/campaigns/999/test-send").status_code == 404
    from backend.core import db
    db.save_settings(db.load_settings(engine).model_copy(update={"gmail_address": ""}), engine)
    assert client.post(f"/campaigns/{cid}/test-send").status_code == 409


def test_dry_run_endpoint_creates_a_job_that_sends_nothing(world, client):
    engine, svc = world
    cid, _ = draft_campaign(engine)
    approve_all(engine, cid)
    job = client.post(f"/campaigns/{cid}/dry-run")
    assert job.status_code == 202 and job.json()["kind"] == "campaign_dry_run"
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    assert jobs.get_job(job.json()["id"], engine)["result"]["would_send"] == 2 and not svc.fake_mailer.sent
    assert client.post("/campaigns/999/dry-run").status_code == 404


def test_messages_list_confirm_and_retry(world, client):
    engine, svc = world
    cid, leads = make_campaign(engine, 3)
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET state='needs_confirmation', error='stopped' WHERE lead_id=:i"), {"i": leads[0]})
        c.execute(text("UPDATE messages SET state='failed', error='550' WHERE lead_id=:i"), {"i": leads[1]})
    allm = client.get(f"/campaigns/{cid}/messages").json()
    assert len(allm) == 3 and allm[0]["lead_name"]
    assert [m["state"] for m in client.get(f"/campaigns/{cid}/messages?state=failed").json()] == ["failed"]
    mid0, mid1 = msg(engine, cid, leads[0])["id"], msg(engine, cid, leads[1])["id"]
    assert client.post(f"/messages/{mid1}/confirm", json={"was_sent": True}).status_code == 409  # not waiting
    assert client.post(f"/messages/{mid0}/retry").status_code == 409  # not failed
    assert client.post(f"/messages/{mid0}/confirm", json={"was_sent": True}).json()["state"] == "sent"
    assert client.post(f"/messages/{mid1}/retry").json()["state"] == "queued"
    assert client.post("/messages/9999/retry").status_code == 404
    assert client.post("/messages/9999/confirm", json={"was_sent": False}).status_code == 404
    with engine.connect() as c:
        assert c.execute(text("SELECT status FROM leads WHERE id=:i"), {"i": leads[0]}).scalar() == "sent"
    assert client.get("/campaigns/999/messages").status_code == 404


def test_stats_and_sending_status(world, client):
    engine, svc = world
    cid, leads = make_campaign(engine, 3)
    snd = sender(engine)
    snd.tick()
    s = client.get(f"/campaigns/{cid}/stats").json()
    assert s["sent"] == 1 and s["queued"] == 2 and s["sent_today"] == 1 and s["daily_cap_effective"] == 25 and s["bounce_rate"] is None
    assert s["next_send_at"] is not None
    st = client.get("/sending/status").json()
    assert st["paused_all"] is False and st["sent_today"] == 1 and st["cap"] == 25 and st["in_flight"] == 0
    assert st["next_send_at"] == s["next_send_at"] and st["worker_alive"] is False and st["caffeinate_active"] is False
    assert client.get("/campaigns/999/stats").status_code == 404


def test_pause_all_and_resume_all_endpoints(world, client):
    engine, svc = world
    a, _ = make_campaign(engine, 2)
    b, _ = make_campaign(engine, 2)
    campaigns.pause(b, "user", None, engine)
    assert client.post("/sending/pause-all").json()["paused_all"] is True
    assert camp(engine, a)["paused_by"] == "all" and camp(engine, b)["paused_by"] == "user"
    assert client.post("/sending/resume-all").json()["paused_all"] is False
    assert camp(engine, a)["state"] == "running" and camp(engine, b)["state"] == "paused"


def test_bounce_pause_resume_only_trips_again_on_new_bounces(world):
    engine, svc = world
    snd = sender(engine)
    # 21 sent, 2 bounced (9.5%): above 5% after at least 20 sends
    cid2, l2 = make_campaign(engine, 30, state="paused")
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET state='sent' WHERE campaign_id=:c AND lead_id IN (%s)" % ",".join(str(i) for i in l2[:21])), {"c": cid2})
        for lid in l2[:2]:
            c.execute(text("INSERT INTO events (lead_id, message_ref, type) SELECT lead_id, id, 'bounce' FROM messages WHERE campaign_id=:c AND lead_id=:l"), {"c": cid2, "l": lid})
        c.execute(text("UPDATE campaigns SET state='running' WHERE id=:c"), {"c": cid2})
    snd.tick()
    row = camp(engine, cid2)
    assert row["state"] == "paused" and row["paused_by"] == "bounce_rate" and "2 of 21" in row["pause_reason"]
    campaigns.resume(cid2, engine)
    snd.tick()
    assert camp(engine, cid2)["state"] == "running"  # acknowledged: no new bounce, no new pause
    with engine.begin() as c:
        c.execute(text("INSERT INTO events (lead_id, message_ref, type) SELECT lead_id, id, 'bounce' FROM messages WHERE campaign_id=:c AND lead_id=:l"), {"c": cid2, "l": l2[2]})
    svc.fake_clock.advance(500)
    snd.tick()
    assert camp(engine, cid2)["paused_by"] == "bounce_rate"


def test_test_send_without_an_app_password_is_a_409_not_a_500(world, client):
    engine, svc = world
    cid, _ = draft_campaign(engine)
    approve_all(engine, cid)
    from backend.core.ports import SmtpAuthError

    def no_mailer():
        raise SmtpAuthError("Gmail is not set up: add your address and App Password in Settings.")

    svc.mailer = no_mailer
    r = client.post(f"/campaigns/{cid}/test-send")
    assert r.status_code == 409 and "App Password" in r.json()["detail"]
