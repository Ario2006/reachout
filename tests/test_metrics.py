"""Dashboard metrics: exact definitions are in the docstring of backend.core.metrics."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.main import create_app
from backend.api.schemas_inbox import Metrics
from backend.core import metrics
from tests import factories
from tests.test_inbox_world import configure, sent_to

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=IST).timestamp()  # Wed 28 Oct, 04:30 UTC
TODAY = "2026-10-28T03:00:00Z"
TOKEN = "t" * 32


@pytest.fixture(autouse=True)
def _local_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def w(engine):
    configure(engine)
    return engine


def ev(engine, lead_id, type_, at=TODAY, code=None, read=False, confirmed=False):
    with engine.begin() as c:
        c.execute(text("INSERT INTO events (lead_id, type, snippet, occurred_at, status_code, read_at, confirmed_at) "
                       "VALUES (:l, :t, 'x', :at, :c, :r, :cf)"),
                  {"l": lead_id, "t": type_, "at": at, "c": code, "r": at if read else None, "cf": at if confirmed else None})


def many(engine, n, campaign_id=None, sent_at=TODAY, prefix="t"):
    return [sent_to(engine, f"{prefix}{i}", "p", campaign_id=campaign_id, sent_at=sent_at)[0] for i in range(n)]


def kpis(engine, **kw):
    return metrics.compute(engine, now=NOW, **kw)


def test_empty_database(w):
    m = kpis(w)
    k = m["kpis"]
    assert k["leads"] == k["sent_total"] == k["delivered"] == k["replies"] == k["awaiting_followup"] == 0
    assert k["reply_rate"] is None and k["bounce_rate"] is None and k["daily_cap"] == 25
    assert [f["count"] for f in m["funnel"]] == [0] * 6 and len(m["per_day"]) == 30 and m["attention"] == []
    assert m["per_day"][-1]["day"] == "2026-10-28" and m["per_day"][0]["day"] == "2026-09-29"
    assert m["opens_note"].startswith("Opens are not tracked")
    assert len(kpis(w, range_="7d")["per_day"]) == 7 and len(kpis(w, range_="all")["per_day"]) == 1


def test_definitions_on_a_realistic_cohort(w):
    leads = many(w, 25)
    for lid in leads[:2]:
        ev(w, lid, "bounce", code="5.1.1")
    ev(w, leads[2], "bounce", code="4.2.2")  # soft: not counted
    for lid in leads[3:8]:
        ev(w, lid, "reply")
    ev(w, leads[3], "reply")  # two replies from one lead count once
    ev(w, leads[8], "opt_out")
    ev(w, leads[9], "auto_reply")
    k = kpis(w)["kpis"]
    assert (k["sent_total"], k["bounced"], k["delivered"], k["replies"], k["opted_out"]) == (25, 2, 23, 5, 1)
    assert k["reply_rate"] == 21.7 and k["bounce_rate"] == 8.0 and k["leads"] == 25 and k["sent_today"] == 25
    funnel = {f["stage"]: f["count"] for f in kpis(w)["funnel"]}
    assert funnel == {"leads": 25, "drafted": 25, "approved": 25, "sent": 25, "delivered": 23, "replied": 5}
    crit = [a for a in kpis(w)["attention"] if a["kind"] == "bounce_rate"]
    assert crit and crit[0]["severity"] == "critical" and "8.0%" in crit[0]["message"] and crit[0]["link"] == "/campaigns"


def test_bounce_rate_waits_for_enough_sends(w):
    leads = many(w, 5)
    ev(w, leads[0], "bounce", code="5.1.1")
    k = kpis(w)["kpis"]
    assert k["bounce_rate"] is None and k["bounced"] == 1 and k["delivered"] == 4 and k["reply_rate"] == 0.0
    assert not [a for a in kpis(w)["attention"] if a["kind"] == "bounce_rate"]


def test_followups_are_counted_separately_and_do_not_inflate_sent(w):
    cid = factories.campaign(w, state="running", followups_enabled=1)
    lead, _ = sent_to(w, "a", "p", campaign_id=cid, sent_at=TODAY)
    sent_to(w, "a", "p", campaign_id=cid, kind="followup_1", message_id="<f1@x>", sent_at=TODAY)
    k = kpis(w)["kpis"]
    assert k["sent_total"] == 1 and k["followups_sent"] == 1 and k["sent_today"] == 2  # both count against the cap
    assert kpis(w)["per_day"][-1]["sent"] == 2


def test_range_selects_the_cohort_by_local_first_send_day(w):
    old = many(w, 2, sent_at="2026-10-10T05:00:00Z", prefix="old")
    new = many(w, 3, sent_at="2026-10-26T05:00:00Z", prefix="new")
    ev(w, old[0], "reply", at="2026-10-27T05:00:00Z")  # a recent reply to an old send
    ev(w, new[0], "reply", at="2026-10-27T05:00:00Z")
    assert kpis(w, range_="7d")["kpis"]["sent_total"] == 3 and kpis(w, range_="7d")["kpis"]["replies"] == 1
    assert kpis(w, range_="30d")["kpis"]["sent_total"] == 5 and kpis(w, range_="30d")["kpis"]["replies"] == 2
    a = kpis(w, range_="all")
    assert a["kpis"]["sent_total"] == 5 and a["per_day"][0]["day"] == "2026-10-10"
    # boundary: the 7-day window starts at local midnight six days ago
    edge = sent_to(w, "edge", "p", sent_at="2026-10-21T18:29:59Z")[0]  # 23:59:59 IST on 21 Oct: outside
    edge2 = sent_to(w, "edge2", "p", sent_at="2026-10-21T18:30:00Z")[0]  # 00:00:00 IST on 22 Oct: inside
    assert kpis(w, range_="7d")["kpis"]["sent_total"] == 4 and edge != edge2


def test_per_day_uses_local_days(w):
    sent_to(w, "late", "p", sent_at="2026-10-27T20:00:00Z")  # 01:30 IST on the 28th
    sent_to(w, "early", "p", sent_at="2026-10-27T17:00:00Z")  # 22:30 IST on the 27th
    lead = sent_to(w, "r", "p", sent_at="2026-10-27T17:00:00Z")[0]
    ev(w, lead, "reply", at="2026-10-27T19:00:00Z")  # 00:30 IST on the 28th
    ev(w, lead, "bounce", at="2026-10-27T05:00:00Z", code="5.1.1")
    days = {d["day"]: d for d in kpis(w)["per_day"]}
    assert (days["2026-10-27"]["sent"], days["2026-10-28"]["sent"]) == (2, 1)
    assert days["2026-10-28"]["replies"] == 1 and days["2026-10-27"]["bounces"] == 1


def test_campaign_filter(w):
    c1 = factories.campaign(w, state="running", daily_cap=10)
    c2 = factories.campaign(w, state="running")
    many(w, 3, campaign_id=c1, prefix="a")
    b = many(w, 2, campaign_id=c2, prefix="b")
    ev(w, b[0], "reply")
    factories.draft(w, c1, factories.lead(w), status="needs_review")
    only1, only2 = kpis(w, campaign_id=c1)["kpis"], kpis(w, campaign_id=c2)["kpis"]
    assert (only1["sent_total"], only1["replies"], only1["leads"], only1["daily_cap"]) == (3, 0, 4, 10)
    assert (only2["sent_total"], only2["replies"], only2["leads"], only2["daily_cap"]) == (2, 1, 2, 25)
    assert kpis(w)["kpis"]["sent_total"] == 5 and only1["drafts_awaiting_review"] == 1 and only2["drafts_awaiting_review"] == 0
    assert any(a["kind"] == "needs_review" for a in kpis(w, campaign_id=c1)["attention"])
    assert not any(a["kind"] == "needs_review" for a in kpis(w, campaign_id=c2)["attention"])


def test_awaiting_and_due_followups(w):
    cid = factories.campaign(w, state="running", followups_enabled=1)  # default days [4, 9]
    off = factories.campaign(w, state="running", followups_enabled=0)
    due, _ = sent_to(w, "due", "p", campaign_id=cid, sent_at="2026-10-20T05:00:00Z")      # +4d passed
    wait, _ = sent_to(w, "wait", "p", campaign_id=cid, sent_at="2026-10-27T05:00:00Z")    # +4d is 31 Oct
    done1, _ = sent_to(w, "done", "p", campaign_id=cid, sent_at="2026-10-10T05:00:00Z")
    sent_to(w, "done", "p", campaign_id=cid, kind="followup_1", message_id="<d1>", sent_at="2026-10-14T05:00:00Z")
    both, _ = sent_to(w, "both", "p", campaign_id=cid, sent_at="2026-10-01T05:00:00Z")
    for n, k in (("b1", "followup_1"), ("b2", "followup_2")):
        sent_to(w, "both", "p", campaign_id=cid, kind=k, message_id=f"<{n}>", sent_at="2026-10-06T05:00:00Z")
    replied, _ = sent_to(w, "rep", "p", campaign_id=cid, sent_at="2026-10-10T05:00:00Z")
    sent_to(w, "norule", "p", campaign_id=off, sent_at="2026-10-10T05:00:00Z")
    with w.begin() as c:
        c.execute(text("UPDATE leads SET status = 'replied' WHERE id = :i"), {"i": replied})
    k = kpis(w, range_="all")
    # due, wait and done (followup_2 pending: 9 days after 10 Oct has passed) await; both/replied/norule do not
    assert k["kpis"]["awaiting_followup"] == 3
    fu = [a for a in k["attention"] if a["kind"] == "followups_due"]
    assert fu and fu[0]["count"] == 2 and fu[0]["severity"] == "info"
    # awaiting ignores the range filter
    assert kpis(w, range_="7d")["kpis"]["awaiting_followup"] == 3
    # a corrupt schedule never breaks the dashboard
    with w.begin() as c:
        c.execute(text("UPDATE campaigns SET followup_days_json = 'oops' WHERE id = :i"), {"i": cid})
    assert kpis(w, range_="all")["kpis"]["awaiting_followup"] == 0


def test_attention_items(w):
    cid = factories.campaign(w, state="paused", paused_by="bounce_rate", pause_reason="Bounce rate too high")
    factories.campaign(w, state="paused", paused_by="user")
    lead, _ = sent_to(w, "a", "p", campaign_id=cid, sent_at=TODAY)
    ev(w, lead, "reply")
    ev(w, lead, "opt_out")
    ev(w, lead, "reply", read=True)
    with w.begin() as c:
        c.execute(text("UPDATE messages SET state = 'needs_confirmation' WHERE lead_id = :i"), {"i": lead})
        c.execute(text("INSERT INTO imap_state (folder, last_error) VALUES ('INBOX', 'Gmail login failed: nope')"))
    got = {a["kind"]: a for a in kpis(w)["attention"]}
    assert set(got) == {"gmail_login", "campaign_paused", "needs_confirmation", "opt_out_to_confirm", "unread_replies"}
    assert got["gmail_login"]["severity"] == "critical" and "nope" in got["gmail_login"]["message"]
    assert got["campaign_paused"]["count"] == 1 and got["unread_replies"]["count"] == 2  # reply + opt_out unread
    assert got["unread_replies"]["link"] == "/inbox?filter=unread" and got["opt_out_to_confirm"]["link"] == "/inbox?filter=opt_out"
    # the order puts what is broken first
    assert [a["kind"] for a in kpis(w)["attention"]][:2] == ["gmail_login", "campaign_paused"]
    ev(w, lead, "opt_out", confirmed=True)
    assert kpis(w)["attention"]  # still valid


def test_activity_feed(w):
    a, _ = sent_to(w, "a", "p", sent_at="2026-10-27T04:00:00Z")
    b, _ = sent_to(w, "b", "p", sent_at="2026-10-27T04:01:00Z")
    ev(w, a, "reply", at="2026-10-27T06:00:00Z")
    ev(w, b, "bounce", at="2026-10-27T05:00:00Z", code="5.1.1")
    ev(w, b, "opt_out", at="2026-10-27T07:00:00Z")
    ev(w, a, "auto_reply", at="2026-10-27T08:00:00Z")
    sent_to(w, "a", "p", kind="followup_1", message_id="<f>", sent_at="2026-10-27T09:00:00Z")
    feed = metrics.activity(w, 30)
    assert [f["kind"] for f in feed] == ["sent", "auto_reply", "opt_out", "reply", "bounce", "sent", "sent"]
    texts = [f["text"] for f in feed]
    assert texts[0].startswith("Sent a follow-up to") and "Auto-reply from" in texts[1] and "Address suppressed" in texts[2]
    assert texts[3].endswith("replied") and "(5.1.1)" in texts[4] and feed[3]["lead_id"] == a
    assert len(metrics.activity(w, 2)) == 2 and len(metrics.activity(w, 0)) == 1


def test_api_endpoints_match_the_schema(w, memory_keyring):
    many(w, 2)
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=w)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        r = c.get("/metrics", params={"range": "7d"})
        assert r.status_code == 200
        Metrics.model_validate(r.json())
        assert c.get("/metrics", params={"range": "bogus"}).status_code == 422
        assert c.get("/metrics", params={"campaign_id": 1, "range": "all"}).status_code == 200
        assert isinstance(c.get("/activity", params={"limit": 5}).json(), list)
