import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from backend.core import db, leads, outbox, tz
from backend.core.extractor import LeadDraft
from backend.core.models import AppSettings, EmailConfidence as C

# Wednesday 2026-10-28 10:00 in Asia/Kolkata == 04:30 UTC
IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=IST).timestamp()
HOUR = 3600.0


def settings(**kw) -> AppSettings:
    base = dict(daily_cap=25, window_start="00:00", window_end="23:59", send_days=[0, 1, 2, 3, 4, 5, 6],
                max_per_domain_per_day=2, min_minutes_between_same_domain=60, use_recipient_timezone=False)
    return AppSettings(**{**base, **kw})


@pytest.fixture
def world(engine, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")  # the sender's "day" for quotas is the machine's local day: pin it
    time.tzset()
    yield _world(engine)
    monkeypatch.undo()
    time.tzset()


def _world(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO campaigns (name, state, daily_cap, window_start, window_end) VALUES ('c1', 'running', 100, '00:00', '23:59')"))
    return engine


def enq(lead_id, campaign_id, kind, subject, engine):
    """outbox.enqueue needs an approved draft: make one (idempotently) so the queue tests stay about the queue."""
    with engine.begin() as c:
        if c.execute(text("SELECT 1 FROM leads WHERE id=:i"), {"i": lead_id}).first() and c.execute(text("SELECT 1 FROM campaigns WHERE id=:i"), {"i": campaign_id}).first() \
                and kind in outbox.KINDS:
            c.execute(text("INSERT OR IGNORE INTO drafts (campaign_id, lead_id, kind, subject, body, status, approved, approved_at) VALUES (:c, :l, :k, :s, 'Body.', 'approved', 1, datetime('now'))"),
                      {"c": campaign_id, "l": lead_id, "k": kind, "s": subject})
    return outbox.enqueue(lead_id, campaign_id, kind, subject, engine)


def lead(engine, email, conf=C.published_personal, name=""):
    res = leads.ingest([LeadDraft(email=email, name=name, company="Co", domain=email.split("@")[1], email_confidence=conf, source_url="https://x.test/")],
                       run_id=None, mode="service", engine=engine)
    return res.new[0]["id"] if res.new else None


def queue(engine, email, kind="initial", **kw):
    lid = lead(engine, email, **kw) or engine.connect().execute(text("SELECT id FROM leads WHERE email=:e"), {"e": email}).scalar()
    return enq(lid, 1, kind, "Hi", engine)[0]


def state(engine, mid):
    return outbox.get_message(mid, engine)["state"]


# ---- idempotency ----------------------------------------------------------------------------------------------

def test_a_person_is_queued_once_per_campaign_and_kind(world):
    lid = lead(world, "a@acme.ai")
    first, created = enq(lid, 1, "initial", "Hi", world)
    again, created2 = enq(lid, 1, "initial", "Hi again", world)
    assert created and not created2 and first == again
    assert enq(lid, 1, "followup_1", "Re", world)[1]  # a different kind is a different message
    with pytest.raises(ValueError):
        enq(lid, 1, "bogus", "", world)
    with pytest.raises(ValueError):
        enq(9999, 1, "initial", "", world)


def test_the_database_itself_refuses_a_duplicate_message(world):
    lid = lead(world, "a@acme.ai")
    enq(lid, 1, "initial", "Hi", world)
    with pytest.raises(Exception, match="UNIQUE"):
        with world.begin() as c:
            c.execute(text("INSERT INTO messages (lead_id, campaign_id, kind) VALUES (:l, 1, 'initial')"), {"l": lid})


def test_a_sent_message_is_never_claimed_or_requeued(world):
    mid = queue(world, "a@acme.ai")
    r = outbox.claim(mid, NOW, settings(), world)
    assert r.ok and state(world, mid) == "sending"
    assert outbox.mark_sent(mid, r.attempt_id, "<abc@mail>", engine=world) and state(world, mid) == "sent"
    again = outbox.claim(mid, NOW + 5 * HOUR, settings(), world)
    assert not again.ok and again.reason == "not_queued"
    assert enq(world.connect().execute(text("SELECT lead_id FROM messages WHERE id=:i"), {"i": mid}).scalar(), 1, "initial", "x", world) == (mid, False)
    assert outbox.next_eligible(1, NOW + 5 * HOUR, settings(), world) == []


# ---- the crash between `sending` and `sent` --------------------------------------------------------------------

def test_a_crash_after_claiming_never_leads_to_a_second_send(world):
    mid = queue(world, "a@acme.ai")
    r = outbox.claim(mid, NOW, settings(), world)
    assert r.ok
    # ... the process dies here: SMTP may or may not have accepted the message. Nothing ran mark_sent.
    assert state(world, mid) == "sending"

    # a new worker starts
    assert outbox.recover_stale(world, now=NOW + 60) == 1
    m = outbox.get_message(mid, world)
    assert m["state"] == "needs_confirmation" and "Check Gmail" in m["error"]

    # no code path picks it up again, however long we wait
    for later in (NOW + 120, NOW + 25 * HOUR, NOW + 400 * HOUR):
        assert outbox.next_eligible(1, later, settings(), world) == []
        res = outbox.claim(mid, later, settings(), world)
        assert not res.ok and res.reason == "not_queued"
    assert outbox.recover_stale(world) == 0  # idempotent
    # a late result from the dead attempt is ignored too
    assert not outbox.mark_sent(mid, r.attempt_id, "<late@mail>", engine=world) and state(world, mid) == "needs_confirmation"
    assert not outbox.mark_failed(mid, r.attempt_id, "late", engine=world)


def test_the_user_decides_what_happened_to_an_unsure_message(world):
    a, b = queue(world, "a@acme.ai"), queue(world, "b@beta.io")
    for m in (a, b):
        outbox.claim(m, NOW, settings(), world)
    outbox.recover_stale(world)
    assert outbox.confirm(a, was_sent=True, engine=world) and state(world, a) == "sent"
    assert outbox.confirm(b, was_sent=False, engine=world) and state(world, b) == "queued"
    assert not outbox.confirm(a, was_sent=False, engine=world)  # a sent message cannot be re-queued
    assert outbox.claim(b, NOW + 5 * HOUR, settings(), world).ok  # the user said it was not sent: it may go


def test_a_crash_before_claiming_loses_nothing(world):
    mid = queue(world, "a@acme.ai")
    assert outbox.recover_stale(world) == 0 and state(world, mid) == "queued"
    assert outbox.claim(mid, NOW, settings(), world).ok


def test_recover_only_touches_stale_attempts_when_given_an_age(world):
    old, new = queue(world, "a@acme.ai"), queue(world, "b@beta.io")
    outbox.claim(old, NOW - 2 * HOUR, settings(), world)
    outbox.claim(new, NOW, settings(), world)
    assert outbox.recover_stale(world, older_than_s=HOUR, now=NOW) == 1
    assert state(world, old) == "needs_confirmation" and state(world, new) == "sending"


def test_failure_outcomes(world):
    a, b = queue(world, "a@acme.ai"), queue(world, "b@beta.io")
    ra, rb = outbox.claim(a, NOW, settings(), world), outbox.claim(b, NOW, settings(), world)
    assert outbox.mark_failed(a, ra.attempt_id, "connection refused", engine=world) and state(world, a) == "failed"
    assert outbox.mark_failed(b, rb.attempt_id, "timed out after DATA", outcome_unknown=True, engine=world) and state(world, b) == "needs_confirmation"
    assert outbox.next_eligible(1, NOW + 5 * HOUR, settings(), world) == []  # nothing retries by itself
    assert outbox.retry_failed(a, world) and not outbox.retry_failed(b, world) and state(world, a) == "queued"
    assert not outbox.mark_sent(a, "wrong-attempt", "<x>", engine=world)


# ---- guards ------------------------------------------------------------------------------------------------------

def test_suppressed_addresses_are_never_claimed_even_after_queueing(world):
    mid = queue(world, "a@acme.ai")
    db.add_suppression("a@acme.ai", "opt_out", world)
    r = outbox.claim(mid, NOW, settings(), world)
    assert not r.ok and r.reason == "suppressed" and state(world, mid) == "queued"


def test_the_sql_claim_rechecks_suppression_even_if_the_precheck_passed(world, monkeypatch):
    mid = queue(world, "a@acme.ai")
    real = outbox.check

    def stale_check(*a, **k):  # the pre-check ran just before the address was suppressed
        r = real(*a, **k)
        db.add_suppression("a@acme.ai", "opt_out", world)
        return r

    monkeypatch.setattr(outbox, "check", stale_check)
    r = outbox.claim(mid, NOW, settings(), world)
    assert not r.ok and state(world, mid) == "queued"


@pytest.mark.parametrize("status", ["replied", "bounced", "opted_out", "auto_reply", "closed"])
def test_leads_that_replied_or_bounced_are_never_followed_up(world, status):
    mid = queue(world, "a@acme.ai", kind="followup_1")
    with world.begin() as c:
        c.execute(text("UPDATE leads SET status=:s"), {"s": status})
    r = outbox.claim(mid, NOW, settings(), world)
    assert not r.ok and r.reason == "lead_status"


def test_guessed_addresses_need_the_setting(world):
    mid = queue(world, "pat.lee@acme.ai", conf=C.guessed, name="Pat Lee")
    assert outbox.claim(mid, NOW, settings(allow_guessed_emails=False), world).reason == "guessed"
    assert outbox.claim(mid, NOW, settings(allow_guessed_emails=True), world).ok


def test_only_running_campaigns_send(world):
    mid = queue(world, "a@acme.ai")
    for st in ("draft", "paused", "done"):
        with world.begin() as c:
            c.execute(text("UPDATE campaigns SET state=:s"), {"s": st})
        assert outbox.claim(mid, NOW, settings(), world).reason == "campaign"


def test_daily_cap_counts_every_campaign_and_is_clamped_to_the_ceiling(world):
    with world.begin() as c:
        c.execute(text("INSERT INTO campaigns (name, state, daily_cap) VALUES ('c2', 'running', 100)"))
    first = outbox.claim(queue(world, "a@a1.io"), NOW, settings(daily_cap=2), world)
    lid = lead(world, "b@b2.io")
    second_msg = enq(lid, 2, "initial", "Hi", world)[0]  # other campaign, same global cap
    assert first.ok and outbox.claim(second_msg, NOW, settings(daily_cap=2), world).ok
    third = outbox.claim(queue(world, "c@c3.io"), NOW, settings(daily_cap=2), world)
    assert not third.ok and third.reason == "daily_cap" and third.retry_at > NOW
    assert outbox.effective_cap(AppSettings(daily_cap=5000), 100) == 100  # the hard ceiling


def test_at_most_two_emails_per_company_domain_per_day_and_an_hour_apart(world):
    a, b, c3 = queue(world, "a@acme.ai"), queue(world, "b@acme.ai"), queue(world, "c@acme.ai")
    assert outbox.claim(a, NOW, settings(), world).ok
    early = outbox.claim(b, NOW + 30 * 60, settings(), world)
    assert not early.ok and early.reason == "domain_gap" and early.retry_at == pytest.approx(NOW + HOUR)
    assert outbox.claim(b, NOW + HOUR + 1, settings(), world).ok
    third = outbox.claim(c3, NOW + 3 * HOUR, settings(), world)
    assert not third.ok and third.reason == "domain_daily_limit"
    tomorrow = NOW + 24 * HOUR
    assert outbox.claim(c3, tomorrow, settings(), world).ok  # a new day


def test_subdomains_count_as_the_same_company(world):
    a, b = queue(world, "a@mail.acme.co.uk"), queue(world, "b@acme.co.uk")
    assert outbox.claim(a, NOW, settings(), world).ok
    assert outbox.claim(b, NOW + 60, settings(), world).reason == "domain_gap"


def test_a_pass_never_picks_two_people_from_one_company(world):
    ids = [queue(world, f"{n}@acme.ai") for n in "abc"] + [queue(world, "z@other.io")]
    picked = outbox.next_eligible(1, NOW, settings(), world, limit=10)
    assert len(picked) == 2 and ids[3] in picked  # one acme, one other


def test_two_workers_racing_for_one_company_slot_cannot_both_win(world):
    ids = [queue(world, f"{n}@acme.ai") for n in "abcdef"]
    wins: list[bool] = []
    barrier = threading.Barrier(len(ids))

    def worker(mid):
        barrier.wait()
        wins.append(outbox.claim(mid, NOW, settings(min_minutes_between_same_domain=60), world).ok)

    ts = [threading.Thread(target=worker, args=(m,)) for m in ids]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(wins) == 1
    assert world.connect().execute(text("SELECT COUNT(*) FROM messages WHERE state='sending'")).scalar() == 1


def test_one_message_claimed_by_two_workers_goes_to_exactly_one(world):
    mid = queue(world, "a@acme.ai")
    wins: list[bool] = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        wins.append(outbox.claim(mid, NOW, settings(), world).ok)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(wins) == 1


# ---- time zones ------------------------------------------------------------------------------------------------------

def test_infer_zone_uses_only_unambiguous_signals():
    assert tz.infer_zone("acme.in").key == "Asia/Kolkata" and tz.infer_zone("foo.co.uk").key == "Europe/London"
    assert tz.infer_zone("acme.com") is None and tz.infer_zone("acme.ai") is None
    assert tz.infer_zone("acme.com", ["Bangalore", "Pune"]).key == "Asia/Kolkata"
    assert tz.infer_zone("acme.com", ["Bangalore", "London"]) is None  # regions disagree
    assert tz.infer_zone("acme.com", ["Narnia"]) is None


def test_send_window_is_judged_in_the_recipients_zone(world):
    mid = queue(world, "a@acme.de")  # Berlin
    s = settings(window_start="09:00", window_end="18:00", use_recipient_timezone=True)
    with world.begin() as c:
        c.execute(text("UPDATE campaigns SET window_start='09:00', window_end='18:00'"))
    # 10:00 IST is 06:30 in Berlin: too early for the recipient even though it is mid-morning for the sender
    assert outbox.claim(mid, NOW, s, world).reason == "window"
    # 14:00 IST is 10:30 in Berlin
    assert outbox.claim(mid, datetime(2026, 10, 28, 14, 0, tzinfo=IST).timestamp(), s, world).ok


def test_the_window_respects_hours_and_allowed_weekdays():
    assert tz.in_send_window(NOW, "09:00", "18:00", [2], IST)
    assert not tz.in_send_window(NOW, "09:00", "18:00", [0, 1], IST)  # Wednesday is not an allowed day
    assert not tz.in_send_window(datetime(2026, 10, 28, 19, 0, tzinfo=IST).timestamp(), "09:00", "18:00", [2], IST)


def test_every_decision_is_traceable_on_the_lead(world):
    mid = queue(world, "a@acme.ai")
    r = outbox.claim(mid, NOW, settings(), world)
    outbox.mark_sent(mid, r.attempt_id, "<m@x>", engine=world)
    lid = world.connect().execute(text("SELECT id FROM leads")).scalar()
    kinds = [t["kind"] for t in leads.get_lead(lid, world)["trace"]]
    assert kinds[-3:] == ["queued", "sending", "sent"]


def test_new_send_settings_have_safe_defaults_and_limits():
    s = AppSettings()
    assert (s.max_per_domain_per_day, s.min_minutes_between_same_domain, s.use_recipient_timezone) == (2, 60, True)
    with pytest.raises(Exception):
        AppSettings(max_per_domain_per_day=0)


# ---- milestone 4: approval, pause-all, not_before, release, dry-run check --------------------------------------
def test_enqueue_needs_an_approved_draft_and_snapshots_it(world):
    lid = lead(world, "a@acme.ai")
    with pytest.raises(outbox.NotApproved):
        outbox.enqueue(lid, 1, "initial", "Hi", world)
    with world.begin() as c:
        c.execute(text("INSERT INTO drafts (campaign_id, lead_id, subject, body, status, approved) VALUES (1, :l, 'Subj', 'Body text', 'pending', 0)"), {"l": lid})
    with pytest.raises(outbox.NotApproved):
        outbox.enqueue(lid, 1, "initial", "Hi", world)  # pending is not approved
    with world.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE lead_id=:l"), {"l": lid})
    mid, created = outbox.enqueue(lid, 1, "initial", "ignored", world, not_before=NOW + 5)
    m = outbox.get_message(mid, world)
    assert created and m["subject"] == "Subj" and m["body"] == "Body text" and m["draft_id"] and m["not_before"] == NOW + 5


def test_check_reports_not_approved_when_the_draft_is_withdrawn_or_edited(world):
    mid = queue(world, "a@acme.ai")
    assert outbox.check(mid, NOW, settings(), world).ok
    with world.begin() as c:
        c.execute(text("UPDATE drafts SET body='changed'"))  # the DB trigger withdraws the approval
    r = outbox.check(mid, NOW, settings(), world)
    assert not r.ok and r.reason == "not_approved"
    assert outbox.claim(mid, NOW, settings(), world).reason == "not_approved" and state(world, mid) == "queued"


def test_check_reports_paused_all_and_not_before(world):
    mid = queue(world, "a@acme.ai")
    outbox.set_paused_all(True, world)
    assert outbox.is_paused_all(world) and outbox.check(mid, NOW, settings(), world).reason == "paused_all"
    assert outbox.claim(mid, NOW, settings(), world).reason == "paused_all"
    outbox.set_paused_all(False, world)
    assert not outbox.is_paused_all(world) and outbox.check(mid, NOW, settings(), world).ok
    with world.begin() as c:
        c.execute(text("UPDATE messages SET not_before=:t"), {"t": NOW + 3600})
    r = outbox.check(mid, NOW, settings(), world)
    assert r.reason == "not_before" and r.retry_at == NOW + 3600
    assert outbox.check(mid, NOW + 3600, settings(), world).ok


def test_set_message_id_and_release_only_for_the_holder_of_the_attempt(world):
    mid = queue(world, "a@acme.ai")
    claim = outbox.claim(mid, NOW, settings(), world)
    assert not outbox.set_message_id(mid, "wrong", "<x@y>", world)
    assert outbox.set_message_id(mid, claim.attempt_id, "<x@y>", world)
    assert outbox.get_message(mid, world)["message_id"] == "<x@y>"
    assert not outbox.release(mid, "wrong", "", world) and state(world, mid) == "sending"
    assert outbox.release(mid, claim.attempt_id, "no connection", world) and state(world, mid) == "queued"
    m = outbox.get_message(mid, world)
    assert m["claimed_at"] is None and m["message_id"] is None and m["error"] == "no connection"
    assert outbox.claim(mid, NOW + 1, settings(), world).ok  # may be claimed again, and counts only once


def test_resync_snapshots_follows_the_reapproved_text_but_never_a_claimed_message(world):
    a, b = queue(world, "a@acme.ai"), queue(world, "b@beta.io")
    assert outbox.claim(b, NOW, settings(), world).ok
    with world.begin() as c:
        c.execute(text("UPDATE drafts SET body='v2'"))  # withdraws the approval (trigger)...
        c.execute(text("UPDATE drafts SET status='approved', approved=1"))  # ...and the user approves the new text
    assert outbox.resync_snapshots(1, world) == 1
    assert outbox.get_message(a, world)["body"] == "v2" and outbox.get_message(b, world)["body"] == "Body."
    assert outbox.resync_snapshots(1, world) == 0


def test_check_draft_dry_run_uses_the_same_rules_without_a_message(world):
    lid = lead(world, "a@acme.ai")
    with world.begin() as c:
        c.execute(text("INSERT INTO drafts (campaign_id, lead_id, subject, body, status, approved) VALUES (1, :l, 'S', 'B', 'approved', 1)"), {"l": lid})
    assert outbox.check_draft(1, lid, "initial", NOW, settings(), world).ok
    assert outbox.check_draft(1, lid, "followup_1", NOW, settings(), world).reason == "missing"
    with world.begin() as c:
        c.execute(text("UPDATE campaigns SET state='draft'"))  # a dry run is done before Start
    assert outbox.check_draft(1, lid, "initial", NOW, settings(), world).ok
    db.add_suppression("a@acme.ai", "x", world)
    assert outbox.check_draft(1, lid, "initial", NOW, settings(), world).reason == "suppressed"
    assert not world.connect().execute(text("SELECT COUNT(*) FROM messages")).scalar()
