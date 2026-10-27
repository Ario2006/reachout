"""The safety matrix (docs/PARALLEL_PLAN.md) rows owned by sending, as named tests, plus property tests over random
interleavings. Everything runs against the fakes or a loopback sink: nothing here can reach a real mail server."""
import itertools
import logging
import smtplib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st
from sqlalchemy import text

from backend.api.main import create_app
from backend.core import campaigns, db, outbox, secrets, services, tz
from backend.core.config import HARD_DAILY_CEILING
from backend.core.mailer import SmtpMailer
from backend.core.ports import SmtpAuthError
from tests import factories
from tests.sending_world import ME, camp, configure, fresh, make_campaign, msg, sender, suppress
from tests.smtp_sink import Sink

PROP = hsettings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
_example = itertools.count(1)


@pytest.fixture
def world(engine, memory_keyring):
    svc, s = fresh(engine)
    yield engine, svc
    services.use(None)


def noon_of_example(svc, engine) -> None:
    """A fresh local noon per example, 60 days apart, so quotas of earlier examples never count."""
    base = tz.local_day_start(1_800_000_000.0) + 12 * 3600 + next(_example) * 60 * 86400
    svc.fake_clock.t = base
    with engine.begin() as c:  # campaigns of earlier examples must not keep sending in this one
        c.execute(text("UPDATE campaigns SET state='done'"))


# ------------------------------------------------------------------ matrix rows
def test_approval_required_and_sent_text_equals_approved_text(world):
    engine, svc = world
    cid = factories.campaign(engine, state="running")
    lid = factories.lead(engine, email="a@acme.test")
    factories.draft(engine, cid, lid, status="pending")
    with pytest.raises(outbox.NotApproved):
        outbox.enqueue(lid, cid, "initial", "x", engine)  # never approved: cannot even be queued
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE lead_id=:i"), {"i": lid})
    mid, _ = outbox.enqueue(lid, cid, "initial", "ignored", engine)
    assert msg(engine, cid, lid)["body"] == "Hi,\n\nBody." and msg(engine, cid, lid)["subject"] == "Hello"
    snd = sender(engine)
    # the user edits the approved draft: the database withdraws the approval, so nothing may go out
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET body='Edited body' WHERE lead_id=:i"), {"i": lid})
    r = outbox.check(mid, svc.fake_clock.now(), db.load_settings(engine), engine)
    assert not r.ok and r.reason == "not_approved"
    assert outbox.claim(mid, svc.fake_clock.now(), db.load_settings(engine), engine).reason == "not_approved"
    assert snd.tick() == 0 and not svc.fake_mailer.sent
    # re-approving the NEW text sends the new text, never the old one
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE lead_id=:i"), {"i": lid})
    assert snd.tick() == 1
    assert "Edited body" in svc.fake_mailer.sent[0].body and "Body." not in svc.fake_mailer.sent[0].body


def test_a_body_that_differs_from_the_approved_text_is_refused_at_claim(world):
    engine, svc = world
    cid, [lid] = make_campaign(engine, 1)
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET body='Something else' WHERE lead_id=:i"), {"i": lid})
    mid = msg(engine, cid, lid)["id"]
    r = outbox.claim(mid, svc.fake_clock.now(), db.load_settings(engine), engine)
    assert not r.ok and r.reason == "not_approved" and "changed" in r.detail
    assert msg(engine, cid, lid)["state"] == "queued"


def test_claim_itself_rechecks_approval_and_pause_all(world):
    """The guards live in the UPDATE too: approval withdrawn or pause-all set between check and claim wins."""
    engine, svc = world
    cid, [lid] = make_campaign(engine, 1)
    mid = msg(engine, cid, lid)["id"]
    real_check = outbox.check
    s = db.load_settings(engine)

    def lying_check(*a, **k):
        return outbox.ClaimResult(True)

    outbox.check = lying_check
    try:
        outbox.set_paused_all(True, engine)
        assert not outbox.claim(mid, svc.fake_clock.now(), s, engine).ok
        outbox.set_paused_all(False, engine)
        with engine.begin() as c:
            c.execute(text("UPDATE drafts SET status='rejected', approved=0 WHERE lead_id=:i"), {"i": lid})
        assert not outbox.claim(mid, svc.fake_clock.now(), s, engine).ok
    finally:
        outbox.check = real_check
    assert msg(engine, cid, lid)["state"] == "queued"


def _bounce_world(engine, sent, bounced, **kw):
    cid, leads = make_campaign(engine, sent + 5, **kw)
    with engine.begin() as c:
        for lid in leads[:sent]:
            c.execute(text("UPDATE messages SET state='sent', claimed_at=1 WHERE lead_id=:i"), {"i": lid})
        for lid in leads[:bounced]:
            c.execute(text("INSERT INTO events (lead_id, message_ref, type) SELECT lead_id, id, 'bounce' FROM messages WHERE lead_id=:i"), {"i": lid})
    return cid


def test_bounce_auto_pause_above_five_percent_after_twenty_sends(world):
    engine, svc = world
    cid = _bounce_world(engine, 20, 2)  # 10% of 20
    assert sender(engine).tick() == 0
    row = camp(engine, cid)
    assert row["state"] == "paused" and row["paused_by"] == "bounce_rate"
    assert "2 of 20" in row["pause_reason"] and "5%" in row["pause_reason"] and not svc.fake_mailer.sent


def test_bounce_guard_needs_twenty_sends_and_more_than_the_limit(world):
    engine, svc = world
    few = _bounce_world(engine, 10, 5)  # 50%, but only 10 sends
    ok = _bounce_world(engine, 40, 2)  # exactly 5%: not above
    sender(engine).tick()
    assert camp(engine, few)["state"] == "running" and camp(engine, ok)["state"] == "running"


def test_bounce_counts_by_lead_when_the_bounce_has_no_message_reference(world):
    engine, svc = world
    cid, leads = make_campaign(engine, 25)
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET state='sent', claimed_at=1 WHERE campaign_id=:c AND lead_id IN (%s)" % ",".join(map(str, leads[:20]))), {"c": cid})
        for lid in leads[:3]:
            c.execute(text("INSERT INTO events (lead_id, type) VALUES (:i, 'bounce')"), {"i": lid})
    assert campaigns.bounce_stats(cid, engine) == (20, 3)
    sender(engine).tick()
    assert camp(engine, cid)["paused_by"] == "bounce_rate"


def test_suppression_applies_to_follow_ups_too(world):
    engine, svc = world
    cid, [lid] = make_campaign(engine, 1)
    factories.draft(engine, cid, lid, status="approved", kind="followup_1", subject="Re: Hello", body="Following up.")
    mid, _ = outbox.enqueue(lid, cid, "followup_1", "", engine)
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET state='sent', claimed_at=1 WHERE lead_id=:i AND kind='initial'"), {"i": lid})
    suppress(engine, "p0@co%dx0.test" % cid)
    r = outbox.check(mid, svc.fake_clock.now(), db.load_settings(engine), engine)
    assert not r.ok and r.reason == "suppressed"
    assert sender(engine).tick() == 0 and not svc.fake_mailer.sent


@pytest.mark.parametrize("mode", ["service", "investor", "internship"])
def test_every_email_handed_to_the_mailer_has_the_opt_out_footer(world, mode):
    engine, svc = world
    cid, [a, b] = make_campaign(engine, 2, mode=mode)
    factories.draft(engine, cid, a, status="approved", kind="followup_1", subject="Re: Hello 0", body="Just following up.")
    outbox.enqueue(a, cid, "followup_1", "", engine)
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET state='sent', claimed_at=1, sent_at=datetime('now') WHERE lead_id=:i AND kind='initial'"), {"i": a})
        c.execute(text("UPDATE leads SET status='sent' WHERE id=:i"), {"i": a})
    snd = sender(engine)
    for _ in range(5):
        snd.tick()
        svc.fake_clock.advance(4000)  # also clears the same-company gap
    campaigns.test_send(cid, engine)
    assert len(svc.fake_mailer.sent) == 3  # an initial, a follow-up and the test send
    for e in svc.fake_mailer.sent:
        assert "reply 'stop'" in e.body, e.subject
        assert e.body.rstrip().endswith("I won't email again.")


def test_a_body_that_already_has_the_footer_does_not_get_it_twice(world):
    engine, svc = world
    from backend.core import footer

    cid, [lid] = make_campaign(engine, 1)
    s = db.load_settings(engine)
    full = footer.ensure_footer("Hi", s, "internship")
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET body=:b WHERE lead_id=:i"), {"b": full, "i": lid})
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE lead_id=:i"), {"i": lid})
    sender(engine).tick()
    assert svc.fake_mailer.sent[0].body.count("reply 'stop'") == 1


def test_no_secret_in_logs_or_api_responses(engine, memory_keyring, caplog):
    """A whole campaign through the real SMTP mailer against a loopback sink: the App Password appears nowhere."""
    pw = "qqwweerrttyyuuii"
    secrets.set_app_password(ME, pw)
    configure(engine)
    sink = Sink().start()
    try:
        svc = services.fakes()
        svc.mailer = lambda: SmtpMailer(ME, secrets.get_app_password(ME) or "", smtp_factory=lambda: smtplib.SMTP("127.0.0.1", sink.port, timeout=5))
        services.use(svc)
        caplog.set_level(logging.DEBUG)
        cid, _ = make_campaign(engine, 2)
        snd = sender(engine)
        for _ in range(3):
            snd.tick()
            svc.fake_clock.advance(300)
        assert len(sink.messages) == 2 and sink.logins[0] == (ME, pw)
        sink.auth_ok = False
        cid2, _ = make_campaign(engine, 1)
        snd.tick()
        assert camp(engine, cid2)["paused_by"] == "smtp_login"
        app = create_app(token="t" * 32, allowed_origins=["http://localhost:5173"], engine=engine)
        with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": "Bearer " + "t" * 32}) as client:
            bodies = [client.get(u).text for u in ("/sending/status", f"/campaigns/{cid}", f"/campaigns/{cid2}", f"/campaigns/{cid}/messages",
                                                   f"/campaigns/{cid}/stats", f"/campaigns/{cid}/readiness", "/campaigns", "/settings")]
        with engine.connect() as c:
            stored = " ".join(str(v) for r in c.execute(text("SELECT error, smtp_result FROM messages")) for v in r if v)
            stored += " ".join(str(r[0]) for r in c.execute(text("SELECT pause_reason FROM campaigns")) if r[0])
            stored += " ".join(str(r[0]) for r in c.execute(text("SELECT message FROM trace_events")))
        assert pw not in caplog.text and pw not in " ".join(bodies) and pw not in stored
    finally:
        sink.stop()
        services.use(None)


def test_login_failure_stops_the_run_for_every_campaign_and_never_loops(world):
    engine, svc = world
    a, _ = make_campaign(engine, 3)
    b, _ = make_campaign(engine, 3)
    svc.fake_mailer.login_error = SmtpAuthError("no")
    attempts = []
    real = svc.fake_mailer.send

    def counting(email):
        attempts.append(email.to_addr)
        raise SmtpAuthError("Gmail rejected the login.")

    svc.fake_mailer.send = counting
    snd = sender(engine)
    for _ in range(20):
        snd.tick()
        svc.fake_clock.advance(1000)
    assert len(attempts) == 1 and camp(engine, a)["paused_by"] == camp(engine, b)["paused_by"] == "smtp_login"
    svc.fake_mailer.send = real


# ------------------------------------------------------------------ properties
class Crash(BaseException):
    pass


op = st.one_of(
    st.just(("tick",)), st.just(("tick",)), st.just(("tick",)),
    st.tuples(st.just("advance"), st.integers(1, 5000)),
    st.just(("day",)),
    st.tuples(st.just("suppress"), st.integers(0, 5)),
    st.just(("arm_crash",)), st.just(("restart",)),
    st.just(("pause_all",)), st.just(("resume_all",)),
    st.just(("not_sent",)),
)


@PROP
@given(ops=st.lists(op, min_size=1, max_size=45), seed=st.integers(0, 10**6))
def test_property_nobody_is_emailed_twice_or_when_suppressed_across_interleavings_and_crashes(engine, memory_keyring, ops, seed):
    svc, s = fresh(engine, daily_cap=4)
    noon_of_example(svc, engine)
    outbox.set_paused_all(False, engine)
    n = next(_example)
    cid, leads = make_campaign(engine, 6, domains=[f"d{n}{i // 2}.test" for i in range(6)], delay_min_s=60, delay_max_s=180)
    emails = [f"p{i}@d{n}{i // 2}.test" for i in range(6)]
    log: list[tuple[float, str, bool, bool, bool]] = []
    mailer = svc.fake_mailer
    real_send = mailer.send

    def spy(email):
        log.append((svc.fake_clock.now(), email.to_addr, db.is_suppressed(email.to_addr, engine), outbox.is_paused_all(engine), camp(engine, cid)["state"] == "running"))
        return real_send(email)

    mailer.send = spy
    snd = sender(engine, seed=seed)
    not_sent: dict[str, int] = {}

    def restart():
        nonlocal snd
        outbox.recover_stale(engine)
        snd = sender(engine, seed=seed + 1)

    for o in ops:
        if o[0] == "tick":
            try:
                snd.tick()
            except Crash:
                restart()
        elif o[0] == "advance":
            svc.fake_clock.advance(o[1])
        elif o[0] == "day":
            svc.fake_clock.advance(86400)
        elif o[0] == "suppress":
            suppress(engine, emails[o[1]])
        elif o[0] == "arm_crash":
            def die(e):
                mailer.crash_after_send = None
                raise Crash()
            mailer.crash_after_send = die
        elif o[0] == "restart":
            restart()
        elif o[0] == "pause_all":
            campaigns.pause_all(engine)
        elif o[0] == "resume_all":
            campaigns.resume_all(engine)
        elif o[0] == "not_sent":
            with engine.connect() as c:
                row = c.execute(text("SELECT id, recipient_email FROM messages WHERE campaign_id=:c AND state='needs_confirmation' ORDER BY id LIMIT 1"), {"c": cid}).first()
            if row:
                campaigns.confirm_message(row[0], False, engine)
                not_sent[row[1]] = not_sent.get(row[1], 0) + 1

    per_addr: dict[str, int] = {}
    for t, addr, was_suppressed, was_paused, running in log:
        assert not was_suppressed, f"sent to a suppressed address {addr}"
        assert not was_paused and running
        per_addr[addr] = per_addr.get(addr, 0) + 1
    for addr, count in per_addr.items():
        assert count <= 1 + not_sent.get(addr, 0), f"{addr} received {count} emails"
    times = sorted(t for t, *_ in log)
    for a, b in zip(times, times[1:]):
        assert b - a >= 60, "two sends closer than the minimum delay"
    days: dict[float, int] = {}
    dom_days: dict[tuple[float, str], int] = {}
    for t, addr, *_ in log:
        d = tz.local_day_start(t)
        days[d] = days.get(d, 0) + 1
        dom_days[(d, addr.split("@")[1])] = dom_days.get((d, addr.split("@")[1]), 0) + 1
    assert all(v <= 4 for v in days.values()) and all(v <= 2 for v in dom_days.values())


@PROP
@pytest.mark.slow
@given(cap=st.integers(1, 150), camp_cap=st.integers(1, 100), leads=st.integers(1, 30))
def test_property_daily_cap_and_hard_ceiling(engine, memory_keyring, cap, camp_cap, leads):
    svc, s = fresh(engine, daily_cap=min(cap, 100), max_per_domain_per_day=10, min_minutes_between_same_domain=0)
    noon_of_example(svc, engine)
    n = next(_example)
    cid, _ = make_campaign(engine, leads, daily_cap=camp_cap, delay_min_s=60, delay_max_s=60, domains=[f"c{n}x{i}.test" for i in range(leads)])
    snd = sender(engine)
    sent = 0
    for _ in range(leads + 3):
        sent += snd.tick()
        svc.fake_clock.advance(61)
    assert sent == len(svc.fake_mailer.sent) == min(leads, min(cap, 100), camp_cap, HARD_DAILY_CEILING)
    assert outbox.effective_cap(db.load_settings(engine).model_copy(update={"daily_cap": 100}), camp_cap) <= HARD_DAILY_CEILING


def test_the_hard_ceiling_beats_any_setting():
    from backend.core.models import AppSettings

    assert outbox.effective_cap(AppSettings.model_construct(daily_cap=5000), 100) == HARD_DAILY_CEILING
    assert outbox.effective_cap(AppSettings.model_construct(daily_cap=5000), 5000) == HARD_DAILY_CEILING


@PROP
@pytest.mark.slow
@given(start_h=st.integers(0, 22), length_h=st.integers(1, 6), days=st.sets(st.integers(0, 6), min_size=1))
def test_property_sends_only_inside_the_send_window(engine, memory_keyring, start_h, length_h, days):
    ws, we = f"{start_h:02d}:00", f"{min(start_h + length_h, 23):02d}:{'00' if start_h + length_h < 24 else '59'}"
    svc, s = fresh(engine, send_days=sorted(days), daily_cap=100, window_start=ws, window_end=we, max_per_domain_per_day=10,
                   min_minutes_between_same_domain=0)
    noon_of_example(svc, engine)
    n = next(_example)
    cid, _ = make_campaign(engine, 12, window_start=ws, window_end=we, delay_min_s=60, delay_max_s=60, domains=[f"w{n}x{i}.test" for i in range(12)])
    snd = sender(engine)
    for _ in range(24 * 7):  # a week in 1 h steps
        snd.tick()
        svc.fake_clock.advance(3600)
    # recompute each send time from the message rows
    with engine.connect() as c:
        times = [r[0] for r in c.execute(text("SELECT claimed_at FROM messages WHERE campaign_id=:c AND claimed_at IS NOT NULL"), {"c": cid})]
    for t in times:
        dt = datetime.fromtimestamp(t)
        hhmm = f"{dt.hour:02d}:{dt.minute:02d}"
        assert dt.weekday() in days and ws <= hhmm <= we, (hhmm, dt.weekday())


@PROP
@pytest.mark.slow
@given(lo=st.integers(60, 300), extra=st.integers(0, 300), seed=st.integers(0, 1000))
def test_property_throttle_between_sends_is_within_the_configured_range(engine, memory_keyring, lo, extra, seed):
    svc, s = fresh(engine, daily_cap=100, max_per_domain_per_day=10, min_minutes_between_same_domain=0)
    noon_of_example(svc, engine)
    n = next(_example)
    cid, _ = make_campaign(engine, 8, delay_min_s=lo, delay_max_s=lo + extra, domains=[f"t{n}x{i}.test" for i in range(8)])
    snd = sender(engine, seed=seed)
    stamps: list[float] = []
    for _ in range(8 * (lo + extra) // 5 + 20):
        if snd.tick():
            stamps.append(svc.fake_clock.now())
        svc.fake_clock.advance(5)
    assert len(stamps) == 8
    for a, b in zip(stamps, stamps[1:]):
        assert lo <= b - a <= lo + extra + 5
