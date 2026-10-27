import threading

import pytest
from sqlalchemy import text

from backend.core import campaigns, db, jobs, outbox, services
from backend.core import sender as sender_mod
from backend.core.job_handlers import HANDLERS
from backend.core.ports import OutcomeUnknownError, RecipientRejected, SmtpAuthError, TransientSendError
from tests import factories
from tests.sending_world import ME, camp, fresh, make_campaign, msg, run_ticks, sender, states, suppress


@pytest.fixture
def world(engine):
    svc, s = fresh(engine)
    yield engine, svc, s
    services.use(None)


def test_sends_one_per_tick_with_jittered_delay_and_footer(world):
    engine, svc, s = world
    cid, leads = make_campaign(engine, 3, delay_min_s=60, delay_max_s=180)
    snd = sender(engine)
    t0 = svc.fake_clock.now()
    assert snd.tick() == 1
    nxt = camp(engine, cid)["next_send_at"]
    assert t0 + 60 <= nxt <= t0 + 180
    assert snd.tick() == 0  # inside the delay: nothing, however often it is asked
    svc.fake_clock.t = nxt - 1
    assert snd.tick() == 0
    svc.fake_clock.t = nxt
    assert snd.tick() == 1
    first = svc.fake_mailer.sent[0]
    assert first.to_addr == "p0@co%dx0.test" % cid and first.from_addr == ME and first.from_name == "Aryan Kumar"
    assert "reply 'stop'" in first.body and first.body.rstrip().endswith("I won't email again.")
    m = msg(engine, cid, leads[0])
    assert m["state"] == "sent" and m["message_id"] == first.message_id and m["sent_at"]
    assert db.load_settings(engine) and engine.connect().execute(text("SELECT status FROM leads WHERE id=:i"), {"i": leads[0]}).scalar() == "sent"


def test_delay_uses_campaign_override_else_settings(world):
    engine, svc, s = world
    cid, _ = make_campaign(engine, 2, delay_min_s=500, delay_max_s=500)
    sender(engine).tick()
    assert camp(engine, cid)["next_send_at"] == pytest.approx(svc.fake_clock.now() + 500)
    cid2, _ = make_campaign(engine, 2)
    sender(engine).tick()
    d = camp(engine, cid2)["next_send_at"] - svc.fake_clock.now()
    assert 60 <= d <= 180


def test_every_email_carries_the_footer_and_stored_message_id(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 4)
    run_ticks(sender(engine), svc, 6)
    assert len(svc.fake_mailer.sent) == 4
    for e in svc.fake_mailer.sent:
        assert "reply 'stop'" in e.body and "Aryan Kumar" in e.body
    assert len({e.message_id for e in svc.fake_mailer.sent}) == 4


def test_campaign_is_done_when_nothing_is_left(world):
    engine, svc, _ = world
    cid, _ = make_campaign(engine, 2)
    snd = sender(engine)
    run_ticks(snd, svc, 5)
    c = camp(engine, cid)
    assert c["state"] == "done" and c["finished_at"] and c["next_send_at"] is None
    assert svc.fake_mailer.sent.__len__() == 2


def test_not_done_while_something_waits_for_the_window(world):
    engine, svc, _ = world
    cid, _ = make_campaign(engine, 1, window_start="03:00", window_end="03:01")
    snd = sender(engine)
    assert snd.tick() == 0 and camp(engine, cid)["state"] == "running"
    snd.idle_backoff_s = 50
    snd.tick()
    assert snd._idle_until[cid] > svc.fake_clock.now()  # backs off instead of re-checking every message each tick


def test_smtp_login_failure_stops_the_run_without_retrying(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 3)
    cid_b, _ = make_campaign(engine, 2)
    calls = []

    def boom(email):
        calls.append(email.to_addr)
        raise SmtpAuthError("Gmail rejected the login.")

    svc.fake_mailer.send = boom
    snd = sender(engine)
    run_ticks(snd, svc, 10)
    assert len(calls) == 1  # one attempt, no retry loop
    for c in (cid, cid_b):
        row = camp(engine, c)
        assert row["state"] == "paused" and row["paused_by"] == "smtp_login" and row["pause_reason"] == "Gmail rejected the login."
    assert msg(engine, cid, leads[0])["state"] == "queued"  # nothing was sent, so nothing is lost


def test_missing_credentials_pause_with_smtp_login(world):
    engine, svc, _ = world
    cid, _ = make_campaign(engine, 2)

    def no_mailer():
        raise SmtpAuthError("Gmail is not set up: add your address and App Password in Settings.")

    svc.mailer = no_mailer
    sender(engine).tick()
    assert camp(engine, cid)["paused_by"] == "smtp_login"


def test_no_gmail_address_pauses(engine):
    svc, _ = fresh(engine, gmail_address="")
    cid, _ = make_campaign(engine, 1)
    sender(engine).tick()
    assert camp(engine, cid)["paused_by"] == "smtp_login" and not svc.fake_mailer.sent
    services.use(None)


def test_recipient_rejected_fails_that_message_and_the_run_continues(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 3)
    bad = "p0@co%dx0.test" % cid
    svc.fake_mailer.fail_with[bad] = RecipientRejected("550 no such user")
    run_ticks(sender(engine), svc, 6)
    st = states(engine, cid)
    assert st[leads[0]] == "failed" and st[leads[1]] == "sent" and st[leads[2]] == "sent"
    assert "550" in msg(engine, cid, leads[0])["error"]
    # explicit retry puts it back in the queue; the campaign (done) reopens
    out = campaigns.retry_message(msg(engine, cid, leads[0])["id"], engine)
    assert out["state"] == "queued" and camp(engine, cid)["state"] == "running"


def test_unknown_outcome_needs_confirmation_and_is_never_resent(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 2)
    svc.fake_mailer.fail_with["p0@co%dx0.test" % cid] = OutcomeUnknownError("dropped after DATA")
    snd = sender(engine)
    run_ticks(snd, svc, 8)
    assert msg(engine, cid, leads[0])["state"] == "needs_confirmation"
    assert [e.to_addr for e in svc.fake_mailer.sent] == ["p1@co%dx1.test" % cid]
    svc.fake_mailer.fail_with.clear()
    run_ticks(snd, svc, 5)
    assert not svc.fake_mailer.to("p0@co%dx0.test" % cid)  # still not resent
    out = campaigns.confirm_message(msg(engine, cid, leads[0])["id"], False, engine)  # user says: not sent
    assert out["state"] == "queued"
    run_ticks(snd, svc, 5)
    assert msg(engine, cid, leads[0])["state"] == "sent"


def test_unexpected_exception_is_treated_as_unknown_outcome(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 1)
    svc.fake_mailer.fail_with["p0@co%dx0.test" % cid] = RuntimeError("weird")
    sender(engine).tick()
    m = msg(engine, cid, leads[0])
    assert m["state"] == "needs_confirmation" and "may or may not" in m["error"]


def test_transient_errors_back_off_then_pause_the_campaign_without_failing_messages(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 4)
    svc.fake_mailer.fail_all = TransientSendError("4xx try later")
    snd = sender(engine)
    t0 = svc.fake_clock.now()
    assert snd.tick() == 1 and msg(engine, cid, leads[0])["state"] == "queued"
    assert camp(engine, cid)["next_send_at"] == pytest.approx(t0 + 60)
    svc.fake_clock.t = t0 + 61
    snd.tick()
    assert camp(engine, cid)["next_send_at"] == pytest.approx(t0 + 61 + 120)
    svc.fake_clock.t = t0 + 61 + 121
    snd.tick()  # third in a row: stop trying instead of walking through the whole queue
    row = camp(engine, cid)
    assert row["state"] == "paused" and row["paused_by"] == "smtp_login" and "3 times in a row" in row["pause_reason"]
    assert set(states(engine, cid).values()) == {"queued"}  # nothing was marked failed by the outage


def test_a_success_resets_the_consecutive_transient_count(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 3)
    bad = "p0@co%dx0.test" % cid
    svc.fake_mailer.fail_with[bad] = TransientSendError("4xx")
    snd = sender(engine)
    snd.tick()
    svc.fake_mailer.fail_with.clear()
    run_ticks(snd, svc, 4, step=400)
    assert camp(engine, cid)["state"] != "paused" and snd._transient_run == 0
    assert msg(engine, cid, leads[0])["state"] == "sent"


def test_an_error_before_the_smtp_call_releases_the_claim(world, monkeypatch):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 1)
    monkeypatch.setattr(sender_mod.footer, "ensure_footer", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert sender(engine).tick() == 0
    m = msg(engine, cid, leads[0])
    assert m["state"] == "queued" and m["claimed_at"] is None and not svc.fake_mailer.sent
    monkeypatch.undo()
    snd = sender(engine)
    svc.fake_clock.advance(500)
    assert snd.tick() == 1 and msg(engine, cid, leads[0])["state"] == "sent"


def test_next_send_time_is_written_before_the_call_so_a_crash_cannot_lose_it(world):
    engine, svc, _ = world
    cid, _ = make_campaign(engine, 2)
    seen = []

    class Crash(BaseException):
        pass

    def die(email):
        seen.append(camp(engine, cid)["next_send_at"])
        raise Crash()

    svc.fake_mailer.crash_after_send = die
    with pytest.raises(Crash):
        sender(engine).tick()
    assert seen[0] is not None and camp(engine, cid)["next_send_at"] >= svc.fake_clock.now() + 60


def test_unsendable_queued_messages_are_cancelled_so_the_campaign_can_finish(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 4)
    suppress(engine, "p1@co%dx1.test" % cid)
    with engine.begin() as c:
        for lid, st in ((leads[2], "bounced"), (leads[3], "opted_out")):
            c.execute(text("UPDATE leads SET status=:s WHERE id=:i"), {"s": st, "i": lid})
    snd = sender(engine)
    snd.tick()
    st = states(engine, cid)
    assert st[leads[1]] == st[leads[2]] == st[leads[3]] == "cancelled" and st[leads[0]] == "sent"
    assert "suppression" in msg(engine, cid, leads[1])["error"] and "bounced" in msg(engine, cid, leads[2])["error"]
    run_ticks(snd, svc, 3)
    assert camp(engine, cid)["state"] == "done"
    assert [e.to_addr for e in svc.fake_mailer.sent] == ["p0@co%dx0.test" % cid]


def test_a_followup_campaign_is_not_finished_while_followups_are_due(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 2, followups_enabled=1, followup_days_json="[4, 9]")
    snd = sender(engine)
    run_ticks(snd, svc, 6)
    assert len(svc.fake_mailer.sent) == 2
    assert camp(engine, cid)["state"] == "running"  # both sent, follow-ups still to come
    assert campaigns.followups_pending(cid, svc.fake_clock.now(), engine)
    # one replied, one is suppressed: nobody left to follow up
    with engine.begin() as c:
        c.execute(text("UPDATE leads SET status='replied' WHERE id=:i"), {"i": leads[0]})
    suppress(engine, "p1@co%dx1.test" % cid)
    assert not campaigns.followups_pending(cid, svc.fake_clock.now(), engine)
    snd.tick()
    assert camp(engine, cid)["state"] == "done"


def test_followups_pending_rules(world):
    engine, svc, _ = world
    now = svc.fake_clock.now()
    off, _ = make_campaign(engine, 1)
    assert not campaigns.followups_pending(off, now, engine)  # not enabled
    cid, [lid] = make_campaign(engine, 1, followups_enabled=1, followup_days_json="[4, 9]")
    assert not campaigns.followups_pending(cid, now, engine)  # first email not sent yet
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET state='sent', claimed_at=:t WHERE campaign_id=:c"), {"t": now, "c": cid})
    assert campaigns.followups_pending(cid, now, engine)
    assert not campaigns.followups_pending(cid, now + 11 * 86400, engine)  # past the last window plus a day
    for k, kind in enumerate(("followup_1", "followup_2")):
        factories.draft(engine, cid, lid, status="approved", kind=kind, subject="Re", body=f"f{k}")
        mid, _ = outbox.enqueue(lid, cid, kind, "", engine)
        with engine.begin() as c:
            c.execute(text("UPDATE messages SET state='sent' WHERE id=:i"), {"i": mid})
    assert not campaigns.followups_pending(cid, now, engine)  # every planned follow-up went out


def test_reopen_if_done_lets_the_followup_task_enqueue_again(world):
    engine, svc, _ = world
    cid, [lid] = make_campaign(engine, 1)
    run_ticks(sender(engine), svc, 3)
    assert camp(engine, cid)["state"] == "done"
    factories.draft(engine, cid, lid, status="approved", kind="followup_1", subject="Re", body="f")
    mid, _ = outbox.enqueue(lid, cid, "followup_1", "", engine, not_before=svc.fake_clock.now())
    assert outbox.check(mid, svc.fake_clock.now() + 4000, db.load_settings(engine), engine).reason == "campaign"
    assert campaigns.reopen_if_done(cid, engine) and camp(engine, cid)["state"] == "running" and camp(engine, cid)["finished_at"] is None
    assert not campaigns.reopen_if_done(cid, engine)  # only from done
    assert outbox.check(mid, svc.fake_clock.now() + 4000, db.load_settings(engine), engine).ok


def test_suppression_is_checked_at_send_time(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 3)
    suppress(engine, "p1@co%dx1.test" % cid)  # added after the message was queued
    claim = outbox.claim(msg(engine, cid, leads[1])["id"], svc.fake_clock.now(), db.load_settings(engine), engine)
    assert not claim.ok and claim.reason == "suppressed"  # the claim itself refuses
    run_ticks(sender(engine), svc, 8)
    assert not svc.fake_mailer.to("p1@co%dx1.test" % cid)
    assert msg(engine, cid, leads[1])["state"] == "cancelled"  # and the sender then drops it for good
    assert len(svc.fake_mailer.sent) == 2


def test_blocking_lead_status_is_not_emailed(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 2)
    with engine.begin() as c:
        c.execute(text("UPDATE leads SET status='replied' WHERE id=:i"), {"i": leads[0]})
    run_ticks(sender(engine), svc, 5)
    assert [e.to_addr for e in svc.fake_mailer.sent] == ["p1@co%dx1.test" % cid]


def test_pause_all_blocks_and_resume_all_continues(world):
    engine, svc, _ = world
    cid, _ = make_campaign(engine, 3)
    snd = sender(engine)
    campaigns.pause_all(engine)
    assert camp(engine, cid)["paused_by"] == "all" and snd.tick() == 0
    assert campaigns.sending_status(engine)["paused_all"]
    campaigns.resume_all(engine)
    assert camp(engine, cid)["state"] == "running" and snd.tick() == 1


def test_pause_all_flag_alone_blocks_a_running_campaign(world):
    engine, svc, _ = world
    cid, _ = make_campaign(engine, 2)
    outbox.set_paused_all(True, engine)
    assert sender(engine).tick() == 0 and not svc.fake_mailer.sent


def test_paused_and_draft_campaigns_never_send(world):
    engine, svc, _ = world
    make_campaign(engine, 2, state="paused")
    make_campaign(engine, 2, state="draft")
    assert sender(engine).tick() == 0


def test_events_are_emitted(world):
    engine, svc, _ = world
    cid, _ = make_campaign(engine, 1)
    from backend.core import events

    start = events.latest_id(engine)
    sender(engine).tick()
    types = {e["type"] for e in events.read_since(start, 100, engine)}
    assert {"message", "campaign", "sending"} <= types


def test_crash_between_sending_and_sent_never_resends(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 2)

    class Crash(BaseException):
        pass

    def die(email):
        raise Crash()

    svc.fake_mailer.crash_after_send = die
    snd = sender(engine)
    with pytest.raises(Crash):
        snd.tick()
    assert msg(engine, cid, leads[0])["state"] == "sending" and len(svc.fake_mailer.sent) == 1
    # restart: new process, recovery, new sender
    svc.fake_mailer.crash_after_send = None
    assert outbox.recover_stale(engine) == 1
    snd2 = sender(engine, seed=2)
    run_ticks(snd2, svc, 6)
    assert msg(engine, cid, leads[0])["state"] == "needs_confirmation"
    assert len(svc.fake_mailer.to("p0@co%dx0.test" % cid)) == 1
    assert len(svc.fake_mailer.to("p1@co%dx1.test" % cid)) == 1


# ---- caffeinate ------------------------------------------------------------------------------------------------
class FakeProc:
    def __init__(self, cmd, **kw):
        self.cmd, self.alive = cmd, True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False


def test_caffeinate_only_while_a_run_is_active(world):
    engine, svc, _ = world
    procs = []

    def popen(cmd, **kw):
        procs.append(FakeProc(cmd))
        return procs[-1]

    snd = sender(engine, popen=popen, which=lambda n: "/usr/bin/caffeinate")
    snd.tick()
    assert not procs and campaigns.sending_status(engine)["caffeinate_active"] is False
    cid, _ = make_campaign(engine, 2)
    snd.tick()
    assert len(procs) == 1 and procs[0].cmd[:3] == ["/usr/bin/caffeinate", "-i", "-w"]
    with engine.begin() as c:
        c.execute(text("INSERT INTO worker_heartbeat (id, pid, beat_at) VALUES (1, 1, strftime('%Y-%m-%dT%H:%M:%S+00:00','now')) ON CONFLICT(id) DO UPDATE SET beat_at=excluded.beat_at"))
    assert campaigns.sending_status(engine)["caffeinate_active"] is True
    snd.tick()
    assert len(procs) == 1  # not started twice
    campaigns.pause(cid, "user", None, engine)
    snd.tick()
    assert not procs[0].alive and campaigns.sending_status(engine)["caffeinate_active"] is False


def test_caffeinate_missing_binary_is_skipped_cleanly(world):
    engine, svc, _ = world
    make_campaign(engine, 1)
    snd = sender(engine, which=lambda n: None)
    assert snd.tick() == 1 and snd._caffeinate is None


def test_caffeinate_launch_failure_is_skipped(world):
    engine, svc, _ = world
    make_campaign(engine, 1)

    def popen(cmd, **kw):
        raise OSError("nope")

    snd = sender(engine, popen=popen, which=lambda n: "/x")
    assert snd.tick() == 1 and snd._caffeinate is None


# ---- shutdown --------------------------------------------------------------------------------------------------
def test_shutdown_pauses_running_campaigns_and_restart_resumes_only_those(world):
    engine, svc, _ = world
    running, _ = make_campaign(engine, 2)
    by_user, _ = make_campaign(engine, 2)
    campaigns.pause(by_user, "user", None, engine)
    snd = sender(engine)
    assert snd.shutdown(wait_s=1) is True
    assert camp(engine, running)["state"] == "paused" and camp(engine, running)["paused_by"] == "shutdown"
    assert snd.tick() == 0  # no new work after shutdown began
    assert campaigns.resume_after_restart(engine) == 1
    assert camp(engine, running)["state"] == "running" and camp(engine, by_user)["state"] == "paused"


def test_shutdown_lets_an_in_flight_send_finish(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 2)
    entered, release = threading.Event(), threading.Event()
    real = svc.fake_mailer.send

    def slow(email):
        entered.set()
        assert release.wait(5)
        return real(email)

    svc.fake_mailer.send = slow
    snd = sender(engine)
    t = threading.Thread(target=snd.tick)
    t.start()
    assert entered.wait(5)
    out = {}
    s = threading.Thread(target=lambda: out.update(ok=snd.shutdown(wait_s=5)))
    s.start()
    release.set()
    t.join(5)
    s.join(5)
    assert out["ok"] is True and msg(engine, cid, leads[0])["state"] == "sent"
    assert camp(engine, cid)["paused_by"] == "shutdown"


def test_shutdown_gives_up_waiting_and_leaves_the_message_for_recovery(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 1)
    entered, release = threading.Event(), threading.Event()

    def stuck(email):
        entered.set()
        release.wait(5)
        raise OutcomeUnknownError("late")

    svc.fake_mailer.send = stuck
    snd = sender(engine)
    t = threading.Thread(target=snd.tick)
    t.start()
    assert entered.wait(5)
    assert snd.shutdown(wait_s=0.2) is False
    assert msg(engine, cid, leads[0])["state"] == "sending" and camp(engine, cid)["paused_by"] == "shutdown"
    assert outbox.recover_stale(engine) == 1
    release.set()
    t.join(5)


def test_module_register_and_shutdown(engine):
    from apscheduler.schedulers.background import BackgroundScheduler

    sched = BackgroundScheduler()
    sender_mod.register(sched, engine)
    assert sched.get_job("sender_tick") is not None
    assert sender_mod.shutdown(1) is True


# ---- dry run ---------------------------------------------------------------------------------------------------
def run_dry(engine, cid):
    job = jobs.create_job("campaign_dry_run", {"campaign_id": cid}, engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    return jobs.get_job(job["id"], engine)


def test_dry_run_reports_each_decision_and_sends_nothing(world):
    engine, svc, _ = world
    cid = factories.campaign(engine, state="draft")
    ok = factories.lead(engine, email="ok@good.test")
    sup = factories.lead(engine, email="no@bad.test")
    replied = factories.lead(engine, email="r@x.test", status="replied")
    guessed = factories.lead(engine, email="g@y.test", email_confidence="guessed")
    for lid in (ok, sup, replied, guessed):
        factories.draft(engine, cid, lid, status="approved")
    factories.draft(engine, cid, factories.lead(engine, email="p@pending.test"), status="pending")
    suppress(engine, "no@bad.test")
    j = run_dry(engine, cid)
    assert j["state"] == "succeeded"
    res = j["result"]
    by = {ln["email"]: ln for ln in res["lines"]}
    assert len(by) == 4 and by["ok@good.test"]["decision"] == "would_send"
    for e in ("no@bad.test", "r@x.test", "g@y.test"):
        assert by[e]["decision"] == "blocked" and by[e]["reason"]
    assert res["would_send"] == 1 and res["blocked"] == 3
    assert not svc.fake_mailer.sent and camp(engine, cid)["dry_run_at"]
    assert not states(engine, cid)  # nothing queued either


def test_dry_run_treats_timing_as_deferral_and_flags_already_sent(world):
    engine, svc, _ = world
    cid, leads = make_campaign(engine, 2, window_start="03:00", window_end="03:01")
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET state='sent' WHERE lead_id=:i"), {"i": leads[1]})
    res = run_dry(engine, cid)["result"]
    by = {ln["lead_id"]: ln for ln in res["lines"]}
    assert by[leads[0]]["decision"] == "would_send" and by[leads[0]]["reason"].startswith("Not right now")
    assert by[leads[1]]["decision"] == "blocked" and "sent" in by[leads[1]]["reason"]
