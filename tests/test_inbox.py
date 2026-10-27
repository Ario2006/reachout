"""IMAP poller, threads and the Inbox API. Everything runs on FakeImap and the .eml fixtures; never Gmail."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.main import create_app
from backend.core import db, events, inbox_poller, services
from backend.core.ports import ImapAuthError
from tests.test_inbox_world import ME, configure, eml, events_of, lead_status, run_check, sent_to, use_fakes

TOKEN = "t" * 32


@pytest.fixture(autouse=True)
def _services():
    yield
    services.use(None)


@pytest.fixture
def world(engine):
    configure(engine)
    return engine


def count(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).scalar_one()


# ------------------------------------------------------------------ recording
def test_reply_recorded_with_status_activity_and_sse(world):
    lead, msg = sent_to(world, "acme", "priya")
    fake = use_fakes().fake_imap
    fake.deliver(eml("reply_threaded"))
    res = run_check(world)
    assert res["new_replies"] == 1 and res["checked"] == 1
    ev = events_of(world, lead)
    assert len(ev) == 1 and ev[0]["type"] == "reply" and ev[0]["message_ref"] == msg
    assert ev[0]["imap_message_id"] == "in-acme-1@mail.example.test" and ev[0]["read_at"] is None
    assert "send your resume" in ev[0]["snippet"] and "stop" not in ev[0]["snippet"]
    assert lead_status(world, lead) == "replied"
    assert count(world, "SELECT last_activity_at FROM leads WHERE id = :i", i=lead) == "2026-10-27T05:45:03Z"
    sse = [e for e in events.read_since(0, engine=world) if e["type"] == "inbox"]
    assert sse and sse[0]["payload"] == {"lead_id": lead, "event_id": ev[0]["id"], "type": "reply"}
    assert not db.is_suppressed("priya@acme.test", world)


def test_redelivery_creates_nothing_new(world):
    lead, _ = sent_to(world, "acme", "priya")
    fake = use_fakes().fake_imap
    fake.deliver(eml("reply_threaded"))
    run_check(world)
    fake.deliver(eml("reply_threaded"), "INBOX")  # same Message-ID, new UID
    res = run_check(world)
    assert res["new_replies"] == 0 and len(events_of(world, lead)) == 1
    # the unique index is the backstop even if the pre-check is bypassed
    assert inbox_poller.process_email(world, eml("reply_threaded"), ME, 1.8e9) is None


def test_uidvalidity_reset_rereads_without_duplicates(world):
    lead, _ = sent_to(world, "acme", "priya")
    fake = use_fakes().fake_imap
    fake.deliver(eml("reply_threaded"))
    run_check(world)
    assert count(world, "SELECT last_uid FROM imap_state WHERE folder = 'INBOX'") == 1
    fake.uidvalidity = 2  # mailbox rebuilt: UIDs mean something else now
    other = sent_to(world, "beta", "ravi")[0]
    fake.deliver(eml("reply_no_headers"))
    res = run_check(world)
    assert res["new_replies"] == 1 and len(events_of(world, lead)) == 1 and len(events_of(world, other)) == 1
    assert count(world, "SELECT uidvalidity FROM imap_state WHERE folder = 'INBOX'") == 2
    assert fake.calls[-2:] == [("INBOX", 1), ("INBOX", 0)] or ("INBOX", 0) in fake.calls


def test_state_advances_and_only_new_mail_is_fetched(world):
    sent_to(world, "acme", "priya")
    fake = use_fakes().fake_imap
    fake.deliver(eml("newsletter_unrelated"))
    fake.deliver(eml("malformed_no_headers"))
    res = run_check(world)
    assert res["checked"] == 2 and res["new_replies"] == 0 and res["errors"] == 0
    assert count(world, "SELECT last_uid FROM imap_state WHERE folder = 'INBOX'") == 2
    run_check(world)
    assert fake.calls[-1] == ("INBOX", 2)
    assert count(world, "SELECT COUNT(*) FROM events") == 0


@pytest.mark.parametrize("fixture,tag,person,etype,status", [
    ("autoreply_ooo", "ooo", "ola", "auto_reply", "auto_reply"),
    ("autoreply_outlook_subject_only", "outlook", "tim", "auto_reply", "auto_reply"),
    ("autoreply_precedence_bulk", "bulk", "help", "auto_reply", "auto_reply"),
    ("optout_stop", "stopper", "sam", "opt_out", "opted_out"),
    ("optout_unsubscribe", "unsub", "sam", "opt_out", "opted_out"),
])
def test_auto_reply_and_opt_out(world, fixture, tag, person, etype, status):
    lead, _ = sent_to(world, tag, person)
    use_fakes().fake_imap.deliver(eml(fixture))
    res = run_check(world)
    assert res["auto_replies"] == (etype == "auto_reply") and res["opt_outs"] == (etype == "opt_out") and res["new_replies"] == 0
    assert events_of(world, lead)[0]["type"] == etype and lead_status(world, lead) == status
    assert db.is_suppressed(f"{person}@{tag}.test", world) == (etype == "opt_out")
    if etype == "opt_out":
        assert events_of(world, lead)[0]["confirmed_at"] is None  # flagged for the user
    assert status != "replied"


def test_auto_reply_does_not_count_as_replied(world):
    lead, _ = sent_to(world, "ooo", "ola")
    use_fakes().fake_imap.deliver(eml("autoreply_ooo"))
    run_check(world)
    assert lead_status(world, lead) != "replied"
    from backend.core.outbox import BLOCKING_LEAD_STATUS
    assert lead_status(world, lead) in BLOCKING_LEAD_STATUS  # and still holds back follow-ups


def test_hard_bounce_suppresses_and_marks_bounced(world):
    lead, msg = sent_to(world, "gone", "nobody")
    use_fakes().fake_imap.deliver(eml("bounce_hard_511"))
    res = run_check(world)
    assert res["bounces"] == 1
    ev = events_of(world, lead)[0]
    assert ev["type"] == "bounce" and ev["status_code"] == "5.1.1" and ev["message_ref"] == msg
    assert lead_status(world, lead) == "bounced" and db.is_suppressed("nobody@gone.test", world)
    assert count(world, "SELECT reason FROM suppression WHERE email = 'nobody@gone.test'") == "hard bounce 5.1.1"


def test_soft_bounce_is_recorded_but_does_not_suppress(world):
    lead, _ = sent_to(world, "full", "box")
    use_fakes().fake_imap.deliver(eml("bounce_soft_422"))
    run_check(world)
    ev = events_of(world, lead)[0]
    assert ev["type"] == "bounce" and ev["status_code"] == "4.2.2"
    assert lead_status(world, lead) == "sent" and not db.is_suppressed("box@full.test", world)


def test_bounce_without_original_matches_by_recipient_and_forged_is_ignored(world):
    lead, _ = sent_to(world, "norig", "dead", message_id="<else@x>")
    fake = use_fakes().fake_imap
    fake.deliver(eml("bounce_hard_no_original"))
    fake.deliver(eml("bounce_forged_unknown_recipient"))
    res = run_check(world)
    assert res["bounces"] == 1 and lead_status(world, lead) == "bounced"
    assert not db.is_suppressed("victim@never-emailed.test", world)
    assert count(world, "SELECT COUNT(*) FROM suppression") == 1  # only dead@norig.test (recipient match adds no extras)


def test_ignored_mail_is_not_recorded(world):
    sent_to(world, "acme", "priya")
    sent_to(world, "interest", "dev")
    fake = use_fakes().fake_imap
    for name in ("from_self", "mdn_read_receipt", "reply_wrong_subject"):
        fake.deliver(eml(name))
    assert run_check(world)["checked"] == 3
    assert count(world, "SELECT COUNT(*) FROM events") == 0


# ------------------------------------------------------------------ priority: weaker never overwrites stronger
@pytest.mark.parametrize("start,incoming,expected", [
    ("opted_out", "reply_threaded", "opted_out"),
    ("bounced", "reply_threaded", "bounced"),
    ("closed", "reply_threaded", "closed"),
    ("replied", "autoreply_ooo", "replied"),
    ("opted_out", "autoreply_ooo", "opted_out"),
    ("bounced", "autoreply_ooo", "bounced"),
    ("replied", "optout_stop", "opted_out"),
    ("closed", "bounce_hard_511", "bounced"),
    ("sent", "reply_threaded", "replied"),
    ("auto_reply", "reply_threaded", "replied"),
])
def test_status_priority(world, start, incoming, expected):
    tag, person = {"reply_threaded": ("acme", "priya"), "autoreply_ooo": ("ooo", "ola"),
                   "optout_stop": ("stopper", "sam"), "bounce_hard_511": ("gone", "nobody")}[incoming]
    lead, _ = sent_to(world, tag, person)
    with world.begin() as c:
        c.execute(text("UPDATE leads SET status = :s WHERE id = :i"), {"s": start, "i": lead})
    use_fakes().fake_imap.deliver(eml(incoming))
    run_check(world)
    assert lead_status(world, lead) == expected
    assert len(events_of(world, lead)) == 1  # the event itself is always recorded


def test_opt_out_after_bounce_outranks_bounced_and_suppresses(world):
    lead, _ = sent_to(world, "stopper", "sam")
    with world.begin() as c:
        c.execute(text("UPDATE leads SET status = 'bounced' WHERE id = :i"), {"i": lead})
    use_fakes().fake_imap.deliver(eml("optout_stop"))
    run_check(world)
    assert lead_status(world, lead) == "opted_out"  # opt-out outranks bounced
    assert db.is_suppressed("sam@stopper.test", world)


def test_odd_encodings_and_dates(world):
    l1, _ = sent_to(world, "latin", "jurgen")
    l2, _ = sent_to(world, "weird", "wei")
    l3, _ = sent_to(world, "future", "fu")
    l4, _ = sent_to(world, "html", "hana")
    fake = use_fakes().fake_imap
    for n in ("reply_latin1_qp", "reply_unknown_charset", "reply_future_date", "reply_html_only"):
        fake.deliver(eml(n))
    res = run_check(world)
    assert res["new_replies"] == 4
    assert "Grüße" in events_of(world, l1)[0]["snippet"]
    # a date years in the future is replaced by "now" (the fake clock)
    assert events_of(world, l3)[0]["occurred_at"].startswith("2027-")


def test_mail_without_message_id_is_deduplicated_by_content_hash(world):
    lead, _ = sent_to(world, "acme", "priya")
    fake = use_fakes().fake_imap
    fake.deliver(eml("malformed_bad_date_no_id"))
    fake.deliver(eml("malformed_bad_date_no_id"))
    assert run_check(world)["new_replies"] == 1
    assert events_of(world, lead)[0]["imap_message_id"].startswith("sha256:")


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(inbox_poller.time, "sleep", lambda s: None)


def _fail_on(monkeypatch, marker: bytes, times: int):
    """process_email raises for the email containing `marker`, `times` times in total."""
    real, left = inbox_poller.process_email, [times]

    def flaky(engine, raw, own, now):
        if marker in raw and left[0] > 0:
            left[0] -= 1
            raise RuntimeError("database is locked")
        return real(engine, raw, own, now)

    monkeypatch.setattr(inbox_poller, "process_email", flaky)


def test_a_record_failure_is_retried_once(world, monkeypatch, no_sleep):
    lead, _ = sent_to(world, "acme", "priya")
    use_fakes().fake_imap.deliver(eml("reply_threaded"))
    _fail_on(monkeypatch, b"Priya", 1)
    res = run_check(world)
    assert res["new_replies"] == 1 and "error" not in res and len(events_of(world, lead)) == 1


def test_persistent_record_failure_stops_the_batch_before_that_uid(world, monkeypatch, no_sleep):
    a, _ = sent_to(world, "beta", "ravi")
    b, _ = sent_to(world, "acme", "priya")
    fake = use_fakes().fake_imap
    fake.deliver(eml("reply_no_headers"))  # uid 1: fine
    fake.deliver(eml("reply_threaded"))    # uid 2: keeps failing
    fake.deliver(eml("newsletter_unrelated"))  # uid 3: must not be passed over either
    _fail_on(monkeypatch, b"Priya", 10)
    res = run_check(world)
    assert res["new_replies"] == 1 and "Could not record email 2" in res["error"]
    assert count(world, "SELECT last_uid FROM imap_state WHERE folder = 'INBOX'") == 1  # cursor stays before uid 2
    assert inbox_poller.status(world)["last_error"] == res["error"]
    assert len(events_of(world, a)) == 1 and events_of(world, b) == [] and lead_status(world, b) == "sent"
    monkeypatch.undo()  # the database is healthy again: the next poll picks up uid 2 and 3
    res = run_check(world)
    assert res["new_replies"] == 1 and "error" not in res and len(events_of(world, b)) == 1
    assert count(world, "SELECT last_uid FROM imap_state WHERE folder = 'INBOX'") == 3
    assert inbox_poller.status(world)["last_error"] is None


def test_download_problem_is_reported_as_last_error(world):
    class Stuck:
        last_problem = "Could not download email 2 in INBOX (attempt 1 of 3); will retry."

        def fetch_new(self, folder, after):
            from backend.core.ports import FetchResult
            return FetchResult(1, [])

        def close(self):
            pass

    f = services.fakes()
    f.imap = lambda: Stuck()
    services.use(f)
    res = run_check(world)
    assert "Could not download email 2" in res["error"]
    assert inbox_poller.status(world)["last_error"] == res["error"]


# ------------------------------------------------------------------ login failure and status
def test_login_failure_is_recorded_not_raised_and_not_looped(world):
    fake = use_fakes().fake_imap
    fake.auth_error = ImapAuthError("Gmail rejected the login.")
    res = run_check(world)
    assert res["error"].startswith(inbox_poller.LOGIN_ERROR_PREFIX) and res["new_replies"] == 0
    assert len(fake.calls) == 0
    st = inbox_poller.status(world)
    assert st["last_error"].startswith("Gmail login failed") and st["last_checked_at"] == 1_800_000_000.0 and not st["checking"]
    # scheduled polls back off for an hour; a manual check still works
    assert inbox_poller.scheduled_tick(world, now=1_800_000_100.0) is None
    queued = inbox_poller.scheduled_tick(world, now=1_800_000_000.0 + 3601)
    assert queued is not None
    with world.begin() as c:
        c.execute(text("UPDATE jobs SET state = 'succeeded' WHERE id = :i"), {"i": queued})
    # recovery clears the error
    fake.auth_error = None
    run_check(world)
    assert inbox_poller.status(world)["last_error"] is None


def test_network_error_is_recorded(world):
    class Down:
        def fetch_new(self, folder, after):
            raise OSError("network is down")

        def close(self):
            raise RuntimeError("close must not mask the error")

    f = services.fakes()
    f.imap = lambda: Down()
    services.use(f)
    res = run_check(world)
    assert res["error"] == "Could not check Gmail: network is down"
    assert inbox_poller.status(world)["last_error"] == res["error"]


def test_scheduling(world):
    from apscheduler.schedulers.background import BackgroundScheduler

    sched = BackgroundScheduler()
    inbox_poller.register(sched, world)
    job = sched.get_job("inbox_poll")
    assert job.trigger.interval.total_seconds() == 600  # settings.imap_poll_minutes default
    configure(world, imap_poll_minutes=3)
    sched2 = BackgroundScheduler()
    inbox_poller.register(sched2, world)
    assert sched2.get_job("inbox_poll").trigger.interval.total_seconds() == 180
    # tick: queues one job, never two; nothing without an account
    first = inbox_poller.scheduled_tick(world)
    assert first and inbox_poller.scheduled_tick(world) is None
    assert inbox_poller.status(world)["checking"]
    db.save_settings(db.load_settings(world).model_copy(update={"gmail_address": ""}), world)
    assert inbox_poller.scheduled_tick(world) is None


def test_cancelled_job_stops(world):
    from backend.core import jobs
    from backend.core.jobs import JobContext

    sent_to(world, "acme", "priya")
    use_fakes().fake_imap.deliver(eml("reply_threaded"))
    job = jobs.create_job("inbox_check", {}, world)
    jobs.request_cancel(job["id"], world)
    with pytest.raises(jobs.JobCancelled):
        inbox_poller.run_inbox_check(JobContext(job["id"], world), {})


# ------------------------------------------------------------------ API: threads, status, actions
@pytest.fixture
def client(world, memory_keyring):
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=world)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


def seed_inbox(world):
    a, _ = sent_to(world, "acme", "priya")
    g, _ = sent_to(world, "gone", "nobody")
    s, _ = sent_to(world, "stopper", "sam")
    o, _ = sent_to(world, "ooo", "ola")
    quiet, _ = sent_to(world, "beta", "ravi")
    fake = use_fakes().fake_imap
    for n in ("reply_threaded", "bounce_hard_511", "optout_stop", "autoreply_ooo"):
        fake.deliver(eml(n))
    run_check(world)
    return dict(acme=a, gone=g, stopper=s, ooo=o, quiet=quiet)


def test_threads_list_filters_search_and_unread(client, world):
    ids = seed_inbox(world)
    page = client.get("/threads").json()
    assert page["total"] == 5 and page["unread_total"] == 4
    by = {t["lead"]["id"]: t for t in page["items"]}
    assert by[ids["acme"]]["last_kind"] == "reply" and by[ids["acme"]]["unread"] == 1
    assert by[ids["quiet"]]["last_kind"] == "sent" and by[ids["quiet"]]["unread"] == 0
    assert by[ids["stopper"]]["opt_out_to_confirm"] and not by[ids["acme"]]["opt_out_to_confirm"]
    ids_for = lambda f: {t["lead"]["id"] for t in client.get("/threads", params={"filter": f}).json()["items"]}  # noqa: E731
    assert ids_for("replied") == {ids["acme"]} and ids_for("bounced") == {ids["gone"]}
    assert ids_for("opt_out") == {ids["stopper"]} and ids_for("auto_reply") == {ids["ooo"]}
    assert ids_for("sent") == {ids["quiet"]} and len(ids_for("unread")) == 4
    assert {t["lead"]["id"] for t in client.get("/threads", params={"q": "resume"}).json()["items"]} == {ids["acme"]}
    assert {t["lead"]["id"] for t in client.get("/threads", params={"q": "PRIYA"}).json()["items"]} == {ids["acme"]}
    assert {t["lead"]["id"] for t in client.get("/threads", params={"q": "internship at beta"}).json()["items"]} == {ids["quiet"]}
    assert client.get("/threads", params={"limit": 2, "offset": 4}).json()["items"].__len__() == 1
    first = client.get("/threads").json()["items"][0]
    assert first["last_activity_at"] >= client.get("/threads").json()["items"][-1]["last_activity_at"]
    cid = count(world, "SELECT campaign_id FROM messages WHERE lead_id = :i", i=ids["acme"])
    assert {t["lead"]["id"] for t in client.get("/threads", params={"campaign_id": cid}).json()["items"]} == {ids["acme"]}


def test_open_thread_marks_read_and_builds_gmail_link(client, world):
    ids = seed_inbox(world)
    d = client.get(f"/threads/{ids['acme']}").json()
    kinds = [e["kind"] for e in d["entries"]]
    assert kinds == ["sent", "reply"] and d["entries"][1]["unread"] is True and d["entries"][0]["send_kind"] == "initial"
    assert d["entries"][0]["text"] == "Hi, body." and d["entries"][0]["message_state"] == "sent"
    assert d["gmail_url"].startswith("https://mail.google.com/mail/?authuser=aryan.reachout@gmail.com#search/rfc822msgid%3A")
    assert "sent-acme-1%40reachout.test" in d["gmail_url"] and "password" not in d["gmail_url"].lower()
    again = client.get(f"/threads/{ids['acme']}").json()
    assert again["entries"][1]["unread"] is False
    assert client.get("/threads").json()["unread_total"] == 3
    assert client.get("/threads/99999").status_code == 404
    bounce = client.get(f"/threads/{ids['gone']}").json()["entries"][1]
    assert bounce["kind"] == "bounce" and bounce["status_code"] == "5.1.1"
    stop = client.get(f"/threads/{ids['stopper']}").json()["entries"][1]
    assert stop["kind"] == "opt_out" and stop["needs_confirmation"]
    # a lead with no sent message has no Gmail link
    lone = count(world, "SELECT id FROM leads WHERE email = 'ravi@beta.test'")
    with world.begin() as c:
        c.execute(text("DELETE FROM messages WHERE lead_id = :i"), {"i": lone})
    assert client.get(f"/threads/{lone}").json()["gmail_url"] is None


def test_confirm_opt_out(client, world):
    ids = seed_inbox(world)
    ev = events_of(world, ids["stopper"])[0]["id"]
    assert client.post(f"/events/{ev}/confirm-opt-out").status_code == 204
    assert events_of(world, ids["stopper"])[0]["confirmed_at"]
    assert not any(t["opt_out_to_confirm"] for t in client.get("/threads").json()["items"])
    assert client.post(f"/events/{events_of(world, ids['acme'])[0]['id']}/confirm-opt-out").status_code == 404  # a reply
    assert client.post("/events/99999/confirm-opt-out").status_code == 404
    assert db.is_suppressed("sam@stopper.test", world)  # confirming changes nothing about suppression


def test_status_actions(client, world):
    ids = seed_inbox(world)
    r = client.post(f"/leads/{ids['acme']}/status", json={"outcome": "interested", "notes": " call Tue "})
    assert r.status_code == 200 and r.json()["outcome"] == "interested" and r.json()["status"] == "replied"
    assert count(world, "SELECT notes FROM leads WHERE id = :i", i=ids["acme"]) == "call Tue"
    r = client.post(f"/leads/{ids['acme']}/status", json={"status": "closed", "outcome": "not_interested"})
    assert r.json()["status"] == "closed" and r.json()["outcome"] == "not_interested"
    # closed blocks sending
    from backend.core.outbox import BLOCKING_LEAD_STATUS
    assert "closed" in BLOCKING_LEAD_STATUS
    assert client.post(f"/leads/{ids['acme']}/status", json={"status": "replied"}).status_code == 200  # reopen is fine
    assert client.post(f"/leads/{ids['acme']}/status", json={"outcome": "meeting_booked"}).json()["outcome"] == "meeting_booked"
    assert client.post("/leads/99999/status", json={"notes": "x"}).status_code == 404
    assert client.post(f"/leads/{ids['acme']}/status", json={"outcome": "bogus"}).status_code == 422


@pytest.mark.parametrize("target,code", [("drafted", 422), ("new", 422), ("queued", 422), ("bounced", 422), ("auto_reply", 422),
                                         ("replied", 409), ("sent", 409)])
def test_status_refusals_for_suppressed_and_terminal_leads(client, world, target, code):
    ids = seed_inbox(world)
    r = client.post(f"/leads/{ids['stopper']}/status", json={"status": target})  # opted_out + suppressed
    assert r.status_code == code and r.json()["detail"]
    assert lead_status(world, ids["stopper"]) == "opted_out" and db.is_suppressed("sam@stopper.test", world)


def test_status_can_never_unsuppress(client, world):
    ids = seed_inbox(world)
    lead = ids["gone"]
    for status in ("replied", "sent"):
        assert client.post(f"/leads/{lead}/status", json={"status": status}).status_code == 409
    assert db.is_suppressed("nobody@gone.test", world)
    # suppressed by hand while the lead is still "sent": moving to a sending state is refused, closing is allowed
    q = ids["quiet"]
    db.add_suppression("ravi@beta.test", "manual", world)
    assert client.post(f"/leads/{q}/status", json={"status": "replied"}).status_code == 409
    assert client.post(f"/leads/{q}/status", json={"status": "closed"}).json()["status"] == "closed"
    assert db.is_suppressed("ravi@beta.test", world)


def test_status_replied_needs_a_sent_email_and_manual_opt_out_suppresses(client, world):
    from tests import factories

    fresh = factories.lead(world, email="new@fresh.test")
    r = client.post(f"/leads/{fresh}/status", json={"status": "replied"})
    assert r.status_code == 422 and "No email was sent" in r.json()["detail"]
    assert client.post(f"/leads/{fresh}/status", json={"status": "closed"}).status_code == 200
    q = sent_to(world, "beta", "ravi")[0]
    assert client.post(f"/leads/{q}/status", json={"status": "opted_out"}).json()["status"] == "opted_out"
    assert db.is_suppressed("ravi@beta.test", world)
    assert client.post(f"/leads/{q}/status", json={"status": "opted_out"}).status_code == 200  # same status: no-op


def test_check_now_and_status_endpoint(client, world):
    s = client.get("/inbox/status").json()
    assert s == {"last_checked_at": None, "next_check_at": None, "last_error": None, "checking": False}
    job = client.post("/inbox/check-now")
    assert job.status_code == 202 and job.json()["kind"] == "inbox_check"
    again = client.post("/inbox/check-now").json()
    assert again["id"] == job.json()["id"]  # no second check while one is queued
    assert client.get("/inbox/status").json()["checking"] is True
    use_fakes()
    from backend.core import jobs
    from backend.core.job_handlers import HANDLERS
    jobs.run_job(jobs.claim_next(world), HANDLERS, world)
    done = jobs.get_job(job.json()["id"], world)
    assert done["state"] == "succeeded" and done["result"]["checked"] == 0
    s = client.get("/inbox/status").json()
    assert s["checking"] is False and s["last_checked_at"] and s["next_check_at"] == s["last_checked_at"] + 600


def test_manual_status_cannot_reopen_a_lead_that_answered(client, world):
    ids = seed_inbox(world)
    for key in ("acme", "ooo"):  # replied; auto-reply
        r = client.post(f"/leads/{ids[key]}/status", json={"status": "sent"})
        assert r.status_code == 409 and r.json()["detail"]
        assert lead_status(world, ids[key]) in {"replied", "auto_reply"}
    # a lead that has an auto-reply event but was manually pushed to a weaker status still can not go back to sent
    with world.begin() as c:
        c.execute(text("UPDATE leads SET status = 'new' WHERE id = :i"), {"i": ids["ooo"]})
    assert client.post(f"/leads/{ids['ooo']}/status", json={"status": "sent"}).status_code == 409
    # a quiet lead may still be set to sent
    with world.begin() as c:
        c.execute(text("UPDATE leads SET status = 'closed' WHERE id = :i"), {"i": ids["quiet"]})
    assert client.post(f"/leads/{ids['quiet']}/status", json={"status": "sent"}).status_code == 409  # closed is blocking
    with world.begin() as c:
        c.execute(text("UPDATE leads SET status = 'new' WHERE id = :i"), {"i": ids["quiet"]})
    assert client.post(f"/leads/{ids['quiet']}/status", json={"status": "sent"}).json()["status"] == "sent"


def test_close_keeps_the_stronger_status_and_records_a_note(client, world):
    ids = seed_inbox(world)
    for key, want in (("stopper", "opted_out"), ("gone", "bounced")):
        r = client.post(f"/leads/{ids[key]}/status", json={"status": "closed", "outcome": "not_interested"})
        assert r.status_code == 200 and r.json()["status"] == want and r.json()["outcome"] == "not_interested"
        assert lead_status(world, ids[key]) == want
        assert f"status stays {want.replace('_', ' ')}" in count(world, "SELECT notes FROM leads WHERE id = :i", i=ids[key])
        assert db.is_suppressed({"stopper": "sam@stopper.test", "gone": "nobody@gone.test"}[key], world)
    # the user's own note wins over the automatic one
    r = client.post(f"/leads/{ids['stopper']}/status", json={"status": "closed", "notes": "called them"})
    assert r.json()["status"] == "opted_out" and count(world, "SELECT notes FROM leads WHERE id = :i", i=ids["stopper"]) == "called them"
