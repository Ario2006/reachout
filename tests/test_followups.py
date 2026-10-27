"""Follow-ups (milestone 6): scheduling, threading, skipping rules, approval, API. Fakes only, no mail server."""
import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.main import create_app
from backend.core import campaigns, db, drafts, followups, outbox, services
from backend.core.ports import RecipientRejected
from tests import factories
from tests.sending_world import camp, fresh, make_campaign, msg, run_ticks, sender, suppress

DAY = 86400.0
TOKEN = "t" * 32


@pytest.fixture
def world(engine, memory_keyring):
    svc, _ = fresh(engine)
    yield engine, svc
    services.use(None)


@pytest.fixture
def client(engine, memory_keyring):
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=engine)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


def fu_campaign(engine, svc, n=2, days="[4, 9]", tpl=None, **kw):
    """A running campaign whose first emails have all been sent; returns (cid, lead ids, sender)."""
    body = dict(followup_1_body="Hi {{first_name}}, just following up.", followup_2_body="Last note, {{first_name}}.")
    tid = factories.template(engine, **{**body, **(tpl or {})})
    cid, leads = make_campaign(engine, n, template_id=tid, followups_enabled=1, followup_days_json=days, **kw)
    snd = sender(engine)
    assert run_ticks(snd, svc, n + 2) == n
    return cid, leads, snd


def rows(engine, cid, kind=None):
    out = followups.plan(cid, engine)["rows"]
    return [r for r in out if kind in (None, r["kind"])]


def state_of(engine, cid, lid, kind):
    return next(r for r in rows(engine, cid, kind) if r["lead_id"] == lid)["state"]


def set_status(engine, lid, status):
    with engine.begin() as c:
        c.execute(text("UPDATE leads SET status=:s WHERE id=:i"), {"s": status, "i": lid})


def go(svc, days):
    svc.fake_clock.advance(days * DAY + 1)


def campaign_state(engine, cid):
    with engine.connect() as c:
        return c.execute(text("SELECT state FROM campaigns WHERE id=:c"), {"c": cid}).scalar()


# ------------------------------------------------------------------ scheduling
def test_nothing_is_created_before_the_day_and_the_campaign_stays_open(world):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    go(svc, 3)
    assert followups.tick(engine) == {"drafts": 0, "queued": 0, "reopened": 0}
    assert state_of(engine, cid, a, "followup_1") == "waiting" and state_of(engine, cid, a, "followup_2") == "waiting"
    snd.tick()
    assert camp(engine, cid)["state"] == "running"  # follow-ups are still pending, so the sender does not finish it


def test_due_followup_becomes_an_approved_re_draft_and_a_queued_message_with_headers(world):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    first = msg(engine, cid, a)
    go(svc, 4)
    assert followups.tick(engine) == {"drafts": 2, "queued": 2, "reopened": 0}
    with engine.connect() as c:
        d = c.execute(text("SELECT subject, body, status, approved FROM drafts WHERE campaign_id=:c AND lead_id=:l AND kind='followup_1'"), {"c": cid, "l": a}).one()
    assert d.subject == "Re: Hello 0" and d.status == "approved" and d.approved == 1 and d.body.startswith("Hi Person")
    m = msg(engine, cid, a, "followup_1")
    assert m["state"] == "queued" and m["body"] == d.body and m["subject"] == d.subject
    assert m["in_reply_to"] == first["message_id"] and m["references_text"] == first["message_id"]
    assert m["not_before"] == pytest.approx(first["claimed_at"] + 4 * DAY)
    assert state_of(engine, cid, a, "followup_1") == "queued"


def test_followup_goes_through_the_sender_with_footer_and_thread_headers(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    first = msg(engine, cid, a)
    go(svc, 4)
    followups.tick(engine)
    assert run_ticks(snd, svc, 3) == 1
    e = svc.fake_mailer.sent[-1]
    assert e.subject == "Re: Hello 0" and e.in_reply_to == first["message_id"] and e.references == (first["message_id"],)
    assert "reply 'stop'" in e.body and e.body.startswith("Hi Person")
    assert state_of(engine, cid, a, "followup_1") == "sent"


def test_followup_2_threads_on_both_earlier_messages(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    first = msg(engine, cid, a)
    go(svc, 4)
    followups.tick(engine)
    run_ticks(snd, svc, 3)
    f1 = msg(engine, cid, a, "followup_1")
    assert f1["state"] == "sent"
    go(svc, 4)  # about day 8
    assert followups.tick(engine)["queued"] == 0
    go(svc, 1.5)  # past day 9 counted from the FIRST email (cumulative days)
    assert followups.tick(engine)["queued"] == 1
    f2 = msg(engine, cid, a, "followup_2")
    assert f2["in_reply_to"] == f1["message_id"] and f2["references_text"] == f"{first['message_id']} {f1['message_id']}"
    assert f2["not_before"] == pytest.approx(first["claimed_at"] + 9 * DAY)
    run_ticks(snd, svc, 3)
    e = svc.fake_mailer.sent[-1]
    assert e.subject == "Re: Hello 0" and e.in_reply_to == f1["message_id"] and e.references == (first["message_id"], f1["message_id"])
    assert state_of(engine, cid, a, "followup_2") == "sent"
    go(svc, 30)
    assert followups.tick(engine)["queued"] == 0 and len(svc.fake_mailer.sent) == 3  # never a third follow-up


def test_a_single_configured_day_means_a_single_followup(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1, days="[2]")
    assert [r["kind"] for r in rows(engine, cid)] == ["followup_1"]


def test_re_prefix_is_not_doubled(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET subject='RE: Re: Hello' WHERE campaign_id=:c"), {"c": cid})
    go(svc, 4)
    followups.tick(engine)
    assert msg(engine, cid, a, "followup_1")["subject"] == "Re: Hello"


# ------------------------------------------------------------------ idempotence and crashes
def test_repeated_ticks_never_create_a_second_followup(world):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    go(svc, 4)
    followups.tick(engine)
    for _ in range(3):
        assert followups.tick(engine) == {"drafts": 0, "queued": 0, "reopened": 0}
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM messages WHERE campaign_id=:c AND kind='followup_1'"), {"c": cid}).scalar() == 2
        assert c.execute(text("SELECT COUNT(*) FROM drafts WHERE campaign_id=:c AND kind='followup_1'"), {"c": cid}).scalar() == 2


def test_a_crash_after_the_draft_is_picked_up_by_the_next_tick(world, monkeypatch):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    monkeypatch.setattr(followups, "_enqueue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    followups.tick(engine)
    monkeypatch.undo()
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM messages WHERE kind='followup_1'")).scalar() == 0
    assert state_of(engine, cid, a, "followup_1") == "due"
    assert followups.tick(engine)["queued"] == 1 and followups.tick(engine)["queued"] == 0


def test_a_crash_before_the_headers_leaves_the_message_unsendable_until_repaired(world, monkeypatch):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    first = msg(engine, cid, a)
    go(svc, 4)
    monkeypatch.setattr(followups, "_release", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    followups.tick(engine)
    monkeypatch.undo()
    held = msg(engine, cid, a, "followup_1")
    assert held["state"] == "queued" and held["in_reply_to"] is None
    assert run_ticks(snd, svc, 3) == 0 and not [e for e in svc.fake_mailer.sent if e.subject.startswith("Re:")]  # not sendable without headers
    followups.tick(engine)
    fixed = msg(engine, cid, a, "followup_1")
    assert fixed["in_reply_to"] == first["message_id"] and fixed["not_before"] < 1e14
    assert run_ticks(snd, svc, 3) == 1 and svc.fake_mailer.sent[-1].in_reply_to == first["message_id"]


# ------------------------------------------------------------------ who gets nothing
@pytest.mark.parametrize("status", ["replied", "bounced", "opted_out", "auto_reply", "closed"])
def test_no_followup_for_a_lead_with_a_blocking_status(world, status):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    set_status(engine, a, status)
    go(svc, 4)
    assert followups.tick(engine)["queued"] == 1  # only b
    r = next(r for r in rows(engine, cid, "followup_1") if r["lead_id"] == a)
    assert r["state"] == "skipped" and status.replace("_", " ") in r["reason"]
    assert next(r for r in rows(engine, cid, "followup_2") if r["lead_id"] == a)["state"] == "skipped"
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM messages WHERE lead_id=:l AND kind!='initial'"), {"l": a}).scalar() == 0


def test_no_followup_for_a_suppressed_address(world):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    suppress(engine, f"p0@co{cid}x0.test")
    go(svc, 4)
    followups.tick(engine)
    assert state_of(engine, cid, a, "followup_1") == "skipped" and state_of(engine, cid, b, "followup_1") == "queued"


def test_a_reply_after_queuing_but_before_sending_prevents_the_send(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    followups.tick(engine)
    set_status(engine, a, "replied")  # what the tracker does
    mid = msg(engine, cid, a, "followup_1")["id"]
    r = outbox.check(mid, svc.fake_clock.now(), db.load_settings(engine), engine)
    assert not r.ok and r.reason == "lead_status"
    assert run_ticks(snd, svc, 3) == 0
    m = msg(engine, cid, a, "followup_1")
    assert m["state"] == "cancelled" and "replied" in m["error"]
    assert len(svc.fake_mailer.sent) == 1
    assert state_of(engine, cid, a, "followup_1") == "skipped"


def test_a_suppression_after_queuing_prevents_the_send(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    followups.tick(engine)
    suppress(engine, f"p0@co{cid}x0.test")
    assert run_ticks(snd, svc, 3) == 0 and len(svc.fake_mailer.sent) == 1


def test_followup_2_is_not_created_when_followup_1_failed(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    followups.tick(engine)
    svc.fake_mailer.fail_with[f"p0@co{cid}x0.test"] = RecipientRejected("550 no such user")
    run_ticks(snd, svc, 3)
    assert msg(engine, cid, a, "followup_1")["state"] == "failed"
    go(svc, 20)
    assert followups.tick(engine)["queued"] == 0
    r2 = rows(engine, cid, "followup_2")[0]
    assert r2["state"] == "skipped" and "not sent" in r2["reason"]
    assert "Sending failed" in rows(engine, cid, "followup_1")[0]["reason"]


def test_followup_needs_the_previous_email_to_be_actually_sent(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET state='needs_confirmation' WHERE campaign_id=:c"), {"c": cid})
    go(svc, 20)
    assert followups.tick(engine)["queued"] == 0 and rows(engine, cid) == []


def test_caps_and_pause_all_still_apply_to_followups(world):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET daily_cap=1 WHERE id=:c"), {"c": cid})
    go(svc, 4)
    followups.tick(engine)
    assert run_ticks(snd, svc, 6) == 1  # one a day, like any other email
    outbox.set_paused_all(True, engine)
    assert run_ticks(snd, svc, 3) == 0


# ------------------------------------------------------------------ campaign state
def test_a_done_campaign_is_reopened_when_a_followup_becomes_due(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET state='done', finished_at=datetime('now') WHERE id=:c"), {"c": cid})
    go(svc, 4)
    assert followups.tick(engine) == {"drafts": 1, "queued": 1, "reopened": 1}
    assert camp(engine, cid)["state"] == "running"
    assert run_ticks(snd, svc, 3) == 1


def test_a_done_campaign_with_a_stuck_queued_followup_is_reopened_on_the_next_tick(world, monkeypatch):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    followups.tick(engine)
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET state='done' WHERE id=:c"), {"c": cid})
    assert followups.tick(engine)["reopened"] == 1 and camp(engine, cid)["state"] == "running"


def test_paused_draft_and_disabled_campaigns_get_no_new_followups(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    campaigns.pause(cid, "user", None, engine)
    assert followups.tick(engine)["queued"] == 0
    assert state_of(engine, cid, a, "followup_1") == "due" and "paused" in rows(engine, cid)[0]["reason"]
    campaigns.resume(cid, engine)
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET followups_enabled=0 WHERE id=:c"), {"c": cid})
    assert followups.tick(engine)["queued"] == 0 and rows(engine, cid) == []


# ------------------------------------------------------------------ approval
def test_followups_wait_for_approval_when_the_campaign_requires_it(world):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc, followups_require_approval=1)
    go(svc, 4)
    assert followups.tick(engine) == {"drafts": 2, "queued": 0, "reopened": 0}
    assert state_of(engine, cid, a, "followup_1") == "awaiting_approval"
    assert followups.tick(engine) == {"drafts": 0, "queued": 0, "reopened": 0}
    assert run_ticks(snd, svc, 3) == 0
    with engine.connect() as c:
        did, status = c.execute(text("SELECT id, status FROM drafts WHERE campaign_id=:c AND lead_id=:l AND kind='followup_1'"), {"c": cid, "l": a}).one()
    assert status == "pending"
    drafts.patch_draft(did, {"status": "approved"}, engine)
    assert followups.tick(engine)["queued"] == 1
    assert state_of(engine, cid, a, "followup_1") == "queued" and state_of(engine, cid, b, "followup_1") == "awaiting_approval"
    assert run_ticks(snd, svc, 3) == 1 and msg(engine, cid, a, "followup_1")["in_reply_to"]


def test_editing_an_approved_followup_withdraws_approval_until_approved_again(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    followups.tick(engine)
    with engine.connect() as c:
        did = c.execute(text("SELECT id FROM drafts WHERE kind='followup_1' AND campaign_id=:c"), {"c": cid}).scalar()
    drafts.patch_draft(did, {"body": "Hi, a different note."}, engine)
    assert run_ticks(snd, svc, 3) == 0  # approval was withdrawn: the sender will not send it
    drafts.patch_draft(did, {"status": "approved"}, engine)
    assert run_ticks(snd, svc, 3) == 1 and svc.fake_mailer.sent[-1].body.startswith("Hi, a different note.")


def test_a_rejected_followup_is_never_recreated(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1, followups_require_approval=1)
    go(svc, 4)
    followups.tick(engine)
    with engine.connect() as c:
        did = c.execute(text("SELECT id FROM drafts WHERE kind='followup_1' AND campaign_id=:c"), {"c": cid}).scalar()
    drafts.patch_draft(did, {"status": "rejected"}, engine)
    go(svc, 30)
    assert followups.tick(engine)["drafts"] == 0
    assert state_of(engine, cid, a, "followup_1") == "skipped" and state_of(engine, cid, a, "followup_2") == "skipped"


# ------------------------------------------------------------------ template without text
def test_a_template_without_followup_text_is_skipped_with_a_reason_and_recovers(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1, tpl=dict(followup_1_body=None))
    go(svc, 4)
    assert followups.tick(engine)["queued"] == 0
    r = rows(engine, cid, "followup_1")[0]
    assert r["state"] == "skipped" and "no text for follow-up 1" in r["reason"]
    with engine.connect() as c:
        tid = c.execute(text("SELECT template_id FROM campaigns WHERE id=:c"), {"c": cid}).scalar()
    with engine.begin() as c:
        c.execute(text("UPDATE templates SET followup_1_body='Back again, {{first_name}}.' WHERE id=:t"), {"t": tid})
    assert followups.tick(engine)["queued"] == 1  # the user added the text: still due, so it goes out


def test_a_campaign_without_template_is_skipped_with_a_reason(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET template_id=NULL WHERE id=:c"), {"c": cid})
    go(svc, 4)
    followups.tick(engine)
    assert "no template" in rows(engine, cid, "followup_1")[0]["reason"]


def test_a_template_that_cannot_render_writes_nothing(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1, tpl=dict(followup_1_body="{{ 1/0 }}"))
    go(svc, 4)
    followups.tick(engine)
    followups.tick(engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM drafts WHERE kind='followup_1'")).scalar() == 0
        assert c.execute(text("SELECT COUNT(*) FROM messages WHERE kind='followup_1'")).scalar() == 0


def test_a_render_failure_that_depends_on_the_lead_is_traced_once(world, monkeypatch):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    with engine.begin() as c:
        c.execute(text("UPDATE leads SET trace_id='tr-1' WHERE id=:i"), {"i": a})
    real = followups.composer.render

    def boom(template, values, opener):
        if values:
            raise followups.composer.TemplateError("bad value")
        return real(template, values, opener)

    monkeypatch.setattr(followups.composer, "render", boom)
    go(svc, 4)
    followups.tick(engine)
    followups.tick(engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM drafts WHERE kind='followup_1'")).scalar() == 0
        assert c.execute(text("SELECT COUNT(*) FROM trace_events WHERE message LIKE 'Follow-up not written%'")).scalar() == 1


def test_missing_merge_fields_are_flagged_on_the_draft(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1, tpl=dict(followup_1_body="Hi {{first_name}}, about {{my_summary}}."))
    go(svc, 4)
    followups.tick(engine)
    with engine.connect() as c:
        assert "my_summary" in c.execute(text("SELECT flags_json FROM drafts WHERE kind='followup_1'")).scalar()


# ------------------------------------------------------------------ plan and skip (API)
def test_plan_states_and_counts(world, client):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    plan = client.get(f"/campaigns/{cid}/followups").json()
    assert plan["campaign_id"] == cid and len(plan["rows"]) == 4
    assert plan["counts"] == {"waiting": 4, "due": 0, "awaiting_approval": 0, "queued": 0, "sent": 0, "skipped": 0}
    r = plan["rows"][0]
    assert r["email"].startswith("p") and r["kind"] == "followup_1" and r["state"] == "waiting"
    assert r["due_at"] == pytest.approx(msg(engine, cid, a)["claimed_at"] + 4 * DAY)
    go(svc, 4)
    followups.tick(engine)
    set_status(engine, b, "replied")
    c = client.get(f"/campaigns/{cid}/followups").json()["counts"]
    assert c["queued"] == 1 and c["skipped"] == 2 and c["waiting"] == 1
    assert client.get("/campaigns/999/followups").status_code == 404


def test_skip_a_queued_followup_cancels_it_and_it_never_returns(world, client):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    followups.tick(engine)
    r = client.post(f"/campaigns/{cid}/followups/skip", json={"lead_id": a, "kind": "followup_1", "reason": "Met them at a conference"})
    assert r.status_code == 204
    assert client.post(f"/campaigns/{cid}/followups/skip", json={"lead_id": a, "kind": "followup_1"}).status_code == 204  # idempotent
    m = msg(engine, cid, a, "followup_1")
    assert m["state"] == "cancelled" and m["error"] == "Met them at a conference"
    assert run_ticks(snd, svc, 3) == 0
    go(svc, 30)
    assert followups.tick(engine)["queued"] == 0
    rows_ = client.get(f"/campaigns/{cid}/followups").json()["rows"]
    assert [x["state"] for x in rows_] == ["skipped", "skipped"] and rows_[0]["reason"] == "Met them at a conference"


def test_skip_before_it_is_due_or_while_awaiting_approval(world, client):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc, followups_require_approval=1)
    assert client.post(f"/campaigns/{cid}/followups/skip", json={"lead_id": a, "kind": "followup_1"}).status_code == 204  # waiting
    go(svc, 4)
    followups.tick(engine)
    assert state_of(engine, cid, b, "followup_1") == "awaiting_approval"
    assert client.post(f"/campaigns/{cid}/followups/skip", json={"lead_id": b, "kind": "followup_1", "reason": "no"}).status_code == 204
    assert state_of(engine, cid, a, "followup_1") == "skipped" and state_of(engine, cid, b, "followup_1") == "skipped"
    assert rows(engine, cid, "followup_1")[0]["reason"] == "Skipped by you."
    with engine.connect() as c:
        assert c.execute(text("SELECT status FROM drafts WHERE lead_id=:l AND kind='followup_1'"), {"l": b}).scalar() == "rejected"
    go(svc, 30)
    assert followups.tick(engine) == {"drafts": 0, "queued": 0, "reopened": 0}


def test_skip_errors(world, client):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    followups.tick(engine)
    run_ticks(snd, svc, 3)
    assert client.post(f"/campaigns/{cid}/followups/skip", json={"lead_id": a, "kind": "followup_1"}).status_code == 409  # sent
    assert client.post(f"/campaigns/{cid}/followups/skip", json={"lead_id": 99999, "kind": "followup_1"}).status_code == 404
    assert client.post("/campaigns/99999/followups/skip", json={"lead_id": a, "kind": "followup_1"}).status_code == 404
    assert client.post(f"/campaigns/{cid}/followups/skip", json={"lead_id": a, "kind": "initial"}).status_code == 422


def test_skip_a_failed_followup_gives_up_on_it(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    followups.tick(engine)
    svc.fake_mailer.fail_with[f"p0@co{cid}x0.test"] = RecipientRejected("550")
    run_ticks(snd, svc, 3)
    followups.skip(cid, a, "followup_1", "give up", engine)
    assert msg(engine, cid, a, "followup_1")["state"] == "cancelled"


# ------------------------------------------------------------------ worker hook
def test_register_adds_an_interval_job_and_the_job_ticks(world):
    engine, svc = world
    sched = BackgroundScheduler()
    followups.register(sched, engine)
    job = sched.get_job("followups_tick")
    assert job is not None and job.trigger.interval.total_seconds() == followups.TICK_EVERY_S
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 4)
    job.func()
    assert msg(engine, cid, a, "followup_1")["state"] == "queued"


def test_sent_at_is_the_fallback_when_there_is_no_claim_time(world):
    assert followups._ts({"claimed_at": None, "sent_at": "2027-01-15T08:00:00.000Z"}) == pytest.approx(1_800_000_000.0, abs=86400)
    assert followups._ts({"claimed_at": None, "sent_at": "2027-01-15 08:00:00"}) is not None
    assert followups._ts({"claimed_at": None, "sent_at": "garbage"}) is None
    assert followups._ts({"claimed_at": None, "sent_at": None}) is None
    assert followups._days("not json") == [] and followups._days("[4, 9, 14]") == [4, 9]


# ------------------------------------------------------------------ review round
def test_the_campaign_does_not_flap_between_done_and_running(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    seen = []
    for _ in range(40):  # about 13 days in 8-hour steps, sender and follow-up ticks interleaved
        svc.fake_clock.advance(8 * 3600)
        followups.tick(engine)
        snd.tick()
        seen.append(campaign_state(engine, cid))
        snd.tick()
    assert [m for m in svc.fake_mailer.sent if m.subject.startswith("Re:")].__len__() == 2
    first_done = seen.index("done")
    assert set(seen[:first_done]) == {"running"} and set(seen[first_done:]) == {"done"}  # one transition, no reopening
    assert msg(engine, cid, a, "followup_2")["state"] == "sent"
    assert rows(engine, cid, "followup_2")[0]["state"] == "sent"


def test_turning_followups_off_cancels_queued_ones_and_rejects_pending_drafts(world):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    cid2, (c1,), _ = fu_campaign(engine, svc, n=1, followups_require_approval=1)
    go(svc, 4)
    followups.tick(engine)  # a and b are queued, c1 awaits approval
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET followups_enabled=0"))
    followups.tick(engine)
    for lid in (a, b):
        m = msg(engine, cid, lid, "followup_1")
        assert m["state"] == "cancelled" and m["error"] == "Follow-ups turned off."
    with engine.connect() as c:
        assert c.execute(text("SELECT status FROM drafts WHERE lead_id=:l AND kind='followup_1'"), {"l": c1}).scalar() == "rejected"
    assert run_ticks(snd, svc, 3) == 0
    followups.tick(engine)  # idempotent
    assert msg(engine, cid, a, "initial")["state"] == "sent"


def test_a_template_that_cannot_render_or_has_no_text_shows_a_reason_in_the_plan(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1, tpl=dict(followup_1_body="{{ 1/0 }}"))
    go(svc, 4)
    followups.tick(engine)
    r = rows(engine, cid, "followup_1")[0]
    assert r["state"] == "skipped" and "could not be written" in r["reason"]
    assert rows(engine, cid, "followup_2")[0]["state"] == "skipped"
    cid2, (b,), _ = fu_campaign(engine, svc, n=1, tpl=dict(followup_1_body="  "))
    go(svc, 4)
    assert "no text for follow-up 1" in rows(engine, cid2, "followup_1")[0]["reason"]
    cid3, (d,), _ = fu_campaign(engine, svc, n=1, tpl=dict(followup_1_body="{{ '' }}"))
    go(svc, 4)
    assert "empty" in rows(engine, cid3, "followup_1")[0]["reason"]


# ------------------------------------------------------------------ review round 2
def test_skip_never_answers_ok_while_the_followup_can_still_go_out(world, monkeypatch):
    engine, svc = world
    cid, (a, b), snd = fu_campaign(engine, svc)
    go(svc, 4)
    followups.tick(engine)
    real_cancel = outbox.cancel

    def lost_race(mid, reason="", engine=None):  # the sender claimed it a moment before our cancel
        with engine.begin() as c:
            c.execute(text("UPDATE messages SET state='sending' WHERE id=:i"), {"i": mid})
        return real_cancel(mid, reason, engine)

    monkeypatch.setattr(followups.outbox, "cancel", lost_race)
    with pytest.raises(followups.FollowupConflict):
        followups.skip(cid, a, "followup_1", None, engine)
    assert msg(engine, cid, a, "followup_1")["state"] == "sending"


def test_skip_that_loses_the_insert_to_a_tick_cancels_the_queued_message(world, monkeypatch):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    real = followups.domains.registrable

    def tick_wins(host):  # runs between skip's read and its INSERT: the tick queues the follow-up
        monkeypatch.setattr(followups.domains, "registrable", real)
        go(svc, 4)
        followups.tick(engine)
        return real(host)

    monkeypatch.setattr(followups.domains, "registrable", tick_wins)
    followups.skip(cid, a, "followup_1", "not now", engine)
    m = msg(engine, cid, a, "followup_1")
    assert m["state"] == "cancelled" and m["error"] == "not now"
    assert run_ticks(snd, svc, 3) == 0


def test_a_draft_with_an_empty_merge_field_is_not_auto_approved(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1, tpl=dict(followup_1_body="Hi {{first_name}}, about {{my_summary}}."))
    go(svc, 4)
    assert followups.tick(engine) == {"drafts": 1, "queued": 0, "reopened": 0}
    with engine.connect() as c:
        assert c.execute(text("SELECT status FROM drafts WHERE kind='followup_1'")).scalar() == "pending"
    assert state_of(engine, cid, a, "followup_1") == "awaiting_approval"
    assert run_ticks(snd, svc, 3) == 0


def test_a_long_pause_never_produces_two_followups_within_a_day(world):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    go(svc, 30)  # the app was off for a month: both follow-ups are long overdue
    assert followups.tick(engine)["queued"] == 1
    run_ticks(snd, svc, 3)
    assert followups.tick(engine)["queued"] == 0  # follow-up 2 waits a day after follow-up 1
    r2 = rows(engine, cid, "followup_2")[0]
    f1 = msg(engine, cid, a, "followup_1")
    assert r2["state"] == "waiting" and r2["due_at"] == pytest.approx(f1["claimed_at"] + DAY)
    go(svc, 1)
    assert followups.tick(engine)["queued"] == 1
    run_ticks(snd, svc, 3)
    f2 = msg(engine, cid, a, "followup_2")
    assert f2["state"] == "sent" and f2["claimed_at"] - f1["claimed_at"] >= DAY


def test_a_held_message_in_a_paused_campaign_is_repaired_not_stuck(world, monkeypatch):
    engine, svc = world
    cid, (a,), snd = fu_campaign(engine, svc, n=1)
    first = msg(engine, cid, a)
    go(svc, 4)
    monkeypatch.setattr(followups, "_release", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    followups.tick(engine)
    monkeypatch.undo()
    campaigns.pause(cid, "user", None, engine)
    followups.tick(engine)
    m = msg(engine, cid, a, "followup_1")
    assert m["state"] == "queued" and m["in_reply_to"] == first["message_id"] and m["not_before"] < 1e14
    campaigns.resume(cid, engine)
    assert run_ticks(snd, svc, 3) == 1
