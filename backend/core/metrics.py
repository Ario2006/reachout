"""Dashboard numbers (milestone 5). Read-only: this module never writes anything.

Definitions (exact; the UI shows these numbers as they are):

Scope. `campaign_id` limits everything to leads that have a draft or a message in that campaign. `range` (7d, 30d,
all) selects a COHORT: the leads whose first email (kind `initial`) was sent inside the range, counted in local days.
Replies, bounces and opt-outs are attributed to the cohort lead whenever they arrive, so every rate compares like
with like. `leads`, `drafts_awaiting_review`, `awaiting_followup`, `sent_today` and `daily_cap` are point-in-time and
ignore `range`. The per-day series is by local calendar day (the machine's time zone) over the range.

* leads                    leads in scope (all statuses, not filtered by range).
* drafts_awaiting_review   drafts of kind initial with status pending or needs_review.
* sent_today               emails of any kind sent since local midnight (these count against the daily cap).
* daily_cap                the effective cap: min(settings.daily_cap, campaign cap, the 100/day ceiling).
* sent_total               first emails (kind initial) sent to cohort leads.
* followups_sent           follow-up emails (followup_1, followup_2) sent to cohort leads.
* bounced                  cohort leads with a HARD bounce (a bounce event whose status code is 5.x.x). Soft
                           (4.x.x) bounces are recorded and listed in the Inbox but are not counted here.
* delivered                sent_total - bounced.
* replies                  cohort leads with at least one `reply` event. Auto-replies and opt-out replies are
                           NOT replies; an opt-out is counted in opted_out instead.
* reply_rate               replies / delivered * 100, one decimal; None when delivered is 0.
* bounce_rate              bounced / sent_total * 100, one decimal; None until sent_total reaches
                           settings.bounce_pause_min_sends (a rate over a handful of sends means nothing).
* opted_out                cohort leads with an `opt_out` event.
* awaiting_followup        leads whose initial email was sent in a
                           campaign with follow-ups enabled, that are not replied, bounced, opted out, auto-replied
                           or closed, and whose next follow-up (followup_1, then followup_2 if the schedule has two
                           steps) has not been sent yet. `followups_due` (attention) is the subset whose scheduled
                           day (initial sent_at + campaigns.followup_days_json[step] days) has passed.
* Funnel                   distinct cohort leads at each stage: leads (in scope), drafted (has a draft or a
                           message), approved (draft approved or a message exists), sent, delivered, replied.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta

from sqlalchemy import Connection, Engine, text

from backend.core import db
from backend.core.config import HARD_DAILY_CEILING
from backend.core.outbox import BLOCKING_LEAD_STATUS, effective_cap

OPENS_NOTE = "Opens are not tracked: it is unreliable and would need a public server."
RANGE_DAYS = {"7d": 7, "30d": 30, "all": None}
MAX_ALL_DAYS = 365


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d > 0 else None


def _scope(campaign_id: int | None) -> tuple[str, dict]:
    """SQL fragment limiting `l` (leads) to a campaign."""
    if campaign_id is None:
        return "", {}
    return (" AND (EXISTS (SELECT 1 FROM drafts d WHERE d.lead_id = l.id AND d.campaign_id = :cid)"
            " OR EXISTS (SELECT 1 FROM messages m0 WHERE m0.lead_id = l.id AND m0.campaign_id = :cid))"), {"cid": campaign_id}


def _start_date(range_: str, today: date, first_send: date | None) -> date:
    days = RANGE_DAYS.get(range_, 30)
    if days is not None:
        return today - timedelta(days=days - 1)
    return max(first_send or today, today - timedelta(days=MAX_ALL_DAYS - 1))


def _cohort_cte(range_: str, start: date, campaign_id: int | None) -> tuple[str, dict]:
    scope, params = _scope(campaign_id)
    cond = "" if range_ == "all" else " AND date(m.sent_at, 'localtime') >= :start"
    if range_ != "all":
        params["start"] = start.isoformat()
    return (f"cohort AS (SELECT DISTINCT l.id AS lead_id FROM leads l JOIN messages m ON m.lead_id = l.id "
            f"WHERE m.kind = 'initial' AND m.sent_at IS NOT NULL{cond}{scope})"), params


def compute(engine: Engine, campaign_id: int | None = None, range_: str = "30d", now: float | None = None) -> dict:
    now = time.time() if now is None else now
    settings = db.load_settings(engine)
    today = datetime.fromtimestamp(now).date()
    with engine.connect() as c:
        first = c.execute(text("SELECT MIN(date(sent_at, 'localtime')) FROM messages WHERE sent_at IS NOT NULL")).scalar()
        start = _start_date(range_, today, date.fromisoformat(first) if first else None)
        cte, params = _cohort_cte(range_, start, campaign_id)
        scope, _ = _scope(campaign_id)

        def one(sql: str, extra: dict | None = None) -> int:
            return int(c.execute(text(f"WITH {cte} {sql}"), {**params, **(extra or {})}).scalar() or 0)

        sent_total = one("SELECT COUNT(*) FROM messages m JOIN cohort ON cohort.lead_id = m.lead_id "
                         "WHERE m.kind = 'initial' AND m.sent_at IS NOT NULL" +
                         (" AND m.campaign_id = :cid" if campaign_id is not None else ""))
        followups_sent = one("SELECT COUNT(*) FROM messages m JOIN cohort ON cohort.lead_id = m.lead_id "
                             "WHERE m.kind != 'initial' AND m.sent_at IS NOT NULL" +
                             (" AND m.campaign_id = :cid" if campaign_id is not None else ""))
        ev = "SELECT COUNT(DISTINCT e.lead_id) FROM events e JOIN cohort ON cohort.lead_id = e.lead_id WHERE "
        bounced = one(ev + "e.type = 'bounce' AND e.status_code LIKE '5%'")
        replies = one(ev + "e.type = 'reply'")
        opted_out = one(ev + "e.type = 'opt_out'")
        delivered = max(0, sent_total - bounced)

        leads_n = int(c.execute(text(f"SELECT COUNT(*) FROM leads l WHERE 1=1{scope}"), _scope(campaign_id)[1]).scalar() or 0)
        drafted = int(c.execute(text(
            f"SELECT COUNT(*) FROM leads l WHERE (EXISTS (SELECT 1 FROM drafts d WHERE d.lead_id = l.id) "
            f"OR EXISTS (SELECT 1 FROM messages m WHERE m.lead_id = l.id)){scope}"), _scope(campaign_id)[1]).scalar() or 0)
        approved = int(c.execute(text(
            f"SELECT COUNT(*) FROM leads l WHERE (EXISTS (SELECT 1 FROM drafts d WHERE d.lead_id = l.id AND d.status = 'approved') "
            f"OR EXISTS (SELECT 1 FROM messages m WHERE m.lead_id = l.id)){scope}"), _scope(campaign_id)[1]).scalar() or 0)

        cp = {"cid": campaign_id} if campaign_id is not None else {}
        cfilter = " AND campaign_id = :cid" if campaign_id is not None else ""
        awaiting_review = int(c.execute(text(
            f"SELECT COUNT(*) FROM drafts WHERE kind = 'initial' AND status IN ('pending','needs_review'){cfilter}"), cp).scalar() or 0)
        needs_review = int(c.execute(text(
            f"SELECT COUNT(*) FROM drafts WHERE kind = 'initial' AND status = 'needs_review'{cfilter}"), cp).scalar() or 0)
        sent_today = int(c.execute(text(
            f"SELECT COUNT(*) FROM messages WHERE sent_at IS NOT NULL AND date(sent_at, 'localtime') = :t{cfilter}"),
            {**cp, "t": today.isoformat()}).scalar() or 0)
        cap = _daily_cap(c, settings, campaign_id)
        per_day = _per_day(c, start, today, campaign_id)
        awaiting, due = _followups(c, campaign_id, now)
        attention = _attention(c, settings, campaign_id, bounce_rate=_bounce_rate(bounced, sent_total, settings),
                               bounced=bounced, needs_review=needs_review, due=due, now=now)

    kpis = {"leads": leads_n, "drafts_awaiting_review": awaiting_review, "sent_today": sent_today, "daily_cap": cap,
            "sent_total": sent_total, "replies": replies, "reply_rate": _pct(replies, delivered),
            "bounce_rate": _bounce_rate(bounced, sent_total, settings), "bounced": bounced, "opted_out": opted_out,
            "followups_sent": followups_sent, "awaiting_followup": awaiting, "delivered": delivered}
    funnel = [{"stage": s, "count": n} for s, n in
              (("leads", leads_n), ("drafted", drafted), ("approved", approved), ("sent", sent_total),
               ("delivered", delivered), ("replied", replies))]
    return {"kpis": kpis, "funnel": funnel, "per_day": per_day, "attention": attention, "opens_note": OPENS_NOTE}


def _bounce_rate(bounced: int, sent: int, settings) -> float | None:
    return _pct(bounced, sent) if sent >= settings.bounce_pause_min_sends else None


def _daily_cap(c: Connection, settings, campaign_id: int | None) -> int:
    camp_cap = HARD_DAILY_CEILING
    if campaign_id is not None:
        v = c.execute(text("SELECT daily_cap FROM campaigns WHERE id = :i"), {"i": campaign_id}).scalar()
        camp_cap = int(v) if v else HARD_DAILY_CEILING
    return effective_cap(settings, camp_cap)


def _per_day(c: Connection, start: date, today: date, campaign_id: int | None) -> list[dict]:
    cp = {"cid": campaign_id} if campaign_id is not None else {}
    cf = " AND campaign_id = :cid" if campaign_id is not None else ""
    days: dict[str, dict] = {}
    d = start
    while d <= today:
        days[d.isoformat()] = {"day": d.isoformat(), "sent": 0, "replies": 0, "bounces": 0}
        d += timedelta(days=1)
    p = {**cp, "s": start.isoformat()}
    for day, n in c.execute(text(f"SELECT date(sent_at,'localtime') d, COUNT(*) FROM messages WHERE sent_at IS NOT NULL "
                                 f"AND date(sent_at,'localtime') >= :s{cf} GROUP BY d"), p):
        if day in days:
            days[day]["sent"] = n
    lead_cf = (" AND e.lead_id IN (SELECT lead_id FROM messages WHERE campaign_id = :cid)"
               if campaign_id is not None else "")
    for day, typ, n in c.execute(text(
            f"SELECT date(e.occurred_at,'localtime') d, e.type, COUNT(*) FROM events e WHERE e.type IN ('reply','bounce') "
            f"AND date(e.occurred_at,'localtime') >= :s{lead_cf} GROUP BY d, e.type"), p):
        if day in days:
            days[day]["replies" if typ == "reply" else "bounces"] = n
    return list(days.values())


def _followups(c: Connection, campaign_id: int | None, now: float) -> tuple[int, int]:
    """(awaiting, due) per the module docstring."""
    cf = " AND m.campaign_id = :cid" if campaign_id is not None else ""
    rows = c.execute(text(
        "SELECT m.lead_id, m.campaign_id, m.sent_at, k.followup_days_json, "
        "EXISTS (SELECT 1 FROM messages f WHERE f.lead_id = m.lead_id AND f.campaign_id = m.campaign_id AND f.kind = 'followup_1' AND f.sent_at IS NOT NULL), "
        "EXISTS (SELECT 1 FROM messages f WHERE f.lead_id = m.lead_id AND f.campaign_id = m.campaign_id AND f.kind = 'followup_2' AND f.sent_at IS NOT NULL) "
        "FROM messages m JOIN campaigns k ON k.id = m.campaign_id JOIN leads l ON l.id = m.lead_id "
        f"WHERE m.kind = 'initial' AND m.sent_at IS NOT NULL AND k.followups_enabled = 1 AND l.status NOT IN "
        f"({', '.join(repr(s) for s in BLOCKING_LEAD_STATUS)}){cf}"),
        {"cid": campaign_id} if campaign_id is not None else {}).all()
    awaiting = due = 0
    for _lead, _camp, sent_at, days_json, f1, f2 in rows:
        try:
            days = [float(x) for x in json.loads(days_json or "[]")]
        except (ValueError, TypeError):
            days = []
        step = 0 if not f1 else 1 if not f2 else 2
        if step >= len(days):
            continue
        awaiting += 1
        try:
            t0s = datetime.fromisoformat(sent_at).timestamp()
        except ValueError:
            continue
        if t0s + days[step] * 86400 <= now:
            due += 1
    return awaiting, due


def _attention(c: Connection, settings, campaign_id: int | None, *, bounce_rate: float | None, bounced: int,
               needs_review: int, due: int, now: float) -> list[dict]:
    items: list[dict] = []

    def add(kind: str, severity: str, message: str, count: int, link: str) -> None:
        if count > 0:
            items.append({"kind": kind, "severity": severity, "message": message, "count": count, "link": link})

    err = c.execute(text("SELECT last_error FROM imap_state WHERE last_error IS NOT NULL LIMIT 1")).scalar()
    if err:
        add("gmail_login", "critical", f"Inbox tracking is not working: {err}", 1, "/settings")
    paused = c.execute(text("SELECT COUNT(*) FROM campaigns WHERE state = 'paused' AND paused_by IN ('bounce_rate','smtp_login')"
                            + (" AND id = :cid" if campaign_id is not None else "")),
                       {"cid": campaign_id} if campaign_id is not None else {}).scalar() or 0
    add("campaign_paused", "critical", f"{paused} campaign(s) paused automatically. Open Campaigns to see why.", paused, "/campaigns")
    if bounce_rate is not None and bounce_rate > settings.bounce_pause_pct:
        add("bounce_rate", "critical", f"Bounce rate is {bounce_rate}%, above the {settings.bounce_pause_pct:g}% limit.",
            bounced, "/campaigns")
    add("needs_review", "warning", f"{needs_review} draft(s) flagged for review.", needs_review, "/compose")
    nc = c.execute(text("SELECT COUNT(*) FROM messages WHERE state = 'needs_confirmation'"
                        + (" AND campaign_id = :cid" if campaign_id is not None else "")),
                   {"cid": campaign_id} if campaign_id is not None else {}).scalar() or 0
    add("needs_confirmation", "warning", f"{nc} email(s) need you to confirm whether they were sent.", nc, "/campaigns")
    oc = c.execute(text("SELECT COUNT(*) FROM events WHERE type = 'opt_out' AND confirmed_at IS NULL")).scalar() or 0
    add("opt_out_to_confirm", "warning", f"{oc} opt-out reply(ies) to check. The address is already suppressed.", oc,
        "/inbox?filter=opt_out")
    unread = c.execute(text("SELECT COUNT(*) FROM events WHERE type IN ('reply','opt_out') AND read_at IS NULL")).scalar() or 0
    add("unread_replies", "info", f"{unread} unread reply(ies).", unread, "/inbox?filter=unread")
    add("followups_due", "info", f"{due} follow-up(s) due.", due, "/campaigns")
    return items


def activity(engine: Engine, limit: int = 30) -> list[dict]:
    limit = max(1, min(limit, 200))
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT at, kind, who, detail, lead_id FROM (
              SELECT m.sent_at AS at, 'sent' AS kind, COALESCE(l.name, l.email) AS who, m.kind AS detail, m.lead_id AS lead_id
                FROM messages m JOIN leads l ON l.id = m.lead_id WHERE m.sent_at IS NOT NULL
              UNION ALL
              SELECT e.occurred_at, e.type, COALESCE(l.name, l.email), COALESCE(e.status_code, ''), e.lead_id
                FROM events e JOIN leads l ON l.id = e.lead_id
            ) ORDER BY at DESC LIMIT :n"""), {"n": limit}).all()
    out = []
    for at, kind, who, detail, lead_id in rows:
        text_ = {"sent": f"Sent {'a follow-up' if str(detail).startswith('followup') else 'an email'} to {who}",
                 "reply": f"{who} replied", "auto_reply": f"Auto-reply from {who}",
                 "opt_out": f"{who} asked to stop. Address suppressed.",
                 "bounce": f"Delivery to {who} failed" + (f" ({detail})" if detail else "")}.get(kind, f"{kind}: {who}")
        out.append({"at": at, "kind": kind, "text": text_, "lead_id": lead_id})
    return out
