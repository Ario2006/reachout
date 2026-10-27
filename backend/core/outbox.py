"""The send-safety core: what must be true before any email leaves, and what happens if the app dies mid-send.

There is no SMTP here: `backend.core.sender` drives this *message state machine* through the `Mailer` port, so the
guarantees live in one place and are tested without a mail server:

    queued -> sending -> sent
                     -> failed              (SMTP refused or never connected: nothing was sent)
                     -> needs_confirmation  (outcome unknown: crash, or a timeout after the message was handed over)
    needs_confirmation -> sent | queued      (only by an explicit user decision; never automatically)
    sending -> queued                        (`release`: the server never got the message, e.g. login refused)

* Approval is part of the state machine: `enqueue` refuses unless an approved draft exists and snapshots its text;
  `check` / `claim` refuse (`not_approved`) unless that draft is STILL approved and still says exactly what was queued.
* One message per (campaign, lead, kind), enforced by a unique index. `enqueue` is idempotent.
* `claim` moves queued -> sending in ONE SQL statement that also re-checks suppression, the daily cap, the per-company
  limit and the gap between people at one company, so two workers can never both pass the same quota.
* A message found in `sending` after a restart can never be auto-resent: `recover_stale` turns it into
  `needs_confirmation` and the user decides, because the email may well have gone out.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import Engine, text

from backend.core import db, domains, slog, tz
from backend.core.config import HARD_DAILY_CEILING
from backend.core.models import AppSettings

# Counted against quotas: these may have reached the recipient.
COUNTED = "('sending','sent','needs_confirmation')"
BLOCKING_LEAD_STATUS = ("replied", "bounced", "opted_out", "auto_reply", "closed")
KINDS = ("initial", "followup_1", "followup_2")


@dataclass(frozen=True)
class ClaimResult:
    ok: bool
    reason: str = ""  # machine-readable code when not ok
    detail: str = ""  # one sentence for the UI ("why isn't this sending?")
    attempt_id: str = ""
    retry_at: float | None = None  # epoch seconds when it may become eligible, if time is the only obstacle


def _trace(c, trace_id: str | None, kind: str, message: str) -> None:
    if trace_id:
        c.execute(text("INSERT INTO trace_events (trace_id, kind, message) VALUES (:t, :k, :m)"), {"t": trace_id, "k": kind, "m": message})


class NotApproved(ValueError):
    """There is no approved draft for this (campaign, lead, kind): nothing may be queued."""


def _approved_draft(c, campaign_id: int, lead_id: int, kind: str):
    return c.execute(text("SELECT id, subject, body FROM drafts WHERE campaign_id=:c AND lead_id=:l AND kind=:k AND status='approved' AND approved=1"),
                     {"c": campaign_id, "l": lead_id, "k": kind}).first()


def enqueue(lead_id: int, campaign_id: int, kind: str, subject: str = "", engine: Engine | None = None,
            not_before: float | None = None) -> tuple[int, bool]:
    """Queue one email from the APPROVED draft for (campaign, lead, kind), snapshotting its subject and body (without
    the footer). Returns (message id, created). Asking again returns the existing message and created=False, whatever
    state it is in: a person is never queued twice. `subject` is ignored: the approved draft's subject is what was
    approved. Raises NotApproved when there is no approved draft."""
    engine = engine or db.get_engine()
    if kind not in KINDS:
        raise ValueError(f"Unknown message kind {kind!r}")
    with engine.begin() as c:
        lead = c.execute(text("SELECT email, trace_id FROM leads WHERE id = :i"), {"i": lead_id}).first()
        if lead is None:
            raise ValueError("Lead not found")
        existing = c.execute(text("SELECT id FROM messages WHERE campaign_id=:c AND lead_id=:l AND kind=:k"), {"c": campaign_id, "l": lead_id, "k": kind}).first()
        if existing:
            return int(existing[0]), False
        draft = _approved_draft(c, campaign_id, lead_id, kind)
        if draft is None:
            raise NotApproved("There is no approved draft for this person, so nothing was queued.")
        email = lead[0].lower()
        res = c.execute(text(
            "INSERT OR IGNORE INTO messages (lead_id, campaign_id, kind, subject, body, draft_id, state, recipient_email, recipient_domain, trace_id, not_before) "
            "VALUES (:l, :c, :k, :s, :b, :dr, 'queued', :e, :d, :t, :nb)"),
            {"l": lead_id, "c": campaign_id, "k": kind, "s": draft[1], "b": draft[2], "dr": draft[0], "e": email,
             "d": domains.registrable(domains.split_email(email)[1]), "t": lead[1], "nb": not_before})
        if res.rowcount:
            _trace(c, lead[1], "queued", f"Queued a {kind} email for sending")
            return int(res.lastrowid), True
        row = c.execute(text("SELECT id FROM messages WHERE campaign_id=:c AND lead_id=:l AND kind=:k"), {"c": campaign_id, "l": lead_id, "k": kind}).first()
        return int(row[0]), False


def get_message(message_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        r = c.execute(text("SELECT * FROM messages WHERE id = :i"), {"i": message_id}).mappings().first()
    return dict(r) if r else None


def _context(c, message_id: int) -> dict | None:
    r = c.execute(text(
        "SELECT m.id, m.state, m.kind, m.lead_id, m.campaign_id, m.recipient_email, m.recipient_domain, m.trace_id, "
        "m.body, m.subject, m.not_before, "
        "l.status AS lead_status, l.email_confidence AS conf, l.search_run_id AS run_id, "
        "cp.state AS campaign_state, cp.daily_cap AS campaign_cap, cp.window_start AS cw_start, cp.window_end AS cw_end "
        "FROM messages m JOIN leads l ON l.id = m.lead_id JOIN campaigns cp ON cp.id = m.campaign_id WHERE m.id = :i"), {"i": message_id}).mappings().first()
    return dict(r) if r else None


def _plan_regions(c, run_id: int | None) -> list[str]:
    if not run_id:
        return []
    raw = c.execute(text("SELECT plan_json FROM search_runs WHERE id = :i"), {"i": run_id}).scalar()
    try:
        return list(json.loads(raw).get("regions", [])) if raw else []
    except (ValueError, AttributeError):
        return []


def effective_cap(settings: AppSettings, campaign_cap: int) -> int:
    return max(0, min(settings.daily_cap, campaign_cap, HARD_DAILY_CEILING))


def paused_all(c) -> bool:
    return c.execute(text("SELECT value FROM app_state WHERE key='pause_all'")).scalar() == "1"


def is_paused_all(engine: Engine | None = None) -> bool:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        return paused_all(c)


def set_paused_all(value: bool, engine: Engine | None = None) -> None:
    engine = engine or db.get_engine()
    with engine.begin() as c:
        c.execute(text("INSERT INTO app_state (key, value) VALUES ('pause_all', :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')"),
                  {"v": "1" if value else "0"})


def check(message_id: int, now: float, settings: AppSettings, engine: Engine | None = None) -> ClaimResult:
    """Read-only: may this message be claimed right now? Same rules as `claim`, with a reason when it may not."""
    engine = engine or db.get_engine()
    with engine.connect() as c:
        m = _context(c, message_id)
        if m is None:
            return ClaimResult(False, "missing", "That message does not exist.")
        if m["state"] != "queued":
            return ClaimResult(False, "not_queued", f"The message is {m['state'].replace('_', ' ')}, so it will not be sent again.")
        return _check_ctx(c, m, now, settings, engine)


def _virtual_context(c, campaign_id: int, lead_id: int, kind: str) -> dict | None:
    """The context a message WOULD have if the approved draft were queued now (dry run). The campaign counts as running."""
    r = c.execute(text(
        "SELECT NULL AS id, 'queued' AS state, d.kind AS kind, l.id AS lead_id, cp.id AS campaign_id, lower(l.email) AS recipient_email, "
        "l.trace_id AS trace_id, d.body AS body, d.subject AS subject, NULL AS not_before, l.status AS lead_status, "
        "l.email_confidence AS conf, l.search_run_id AS run_id, 'running' AS campaign_state, cp.daily_cap AS campaign_cap, "
        "cp.window_start AS cw_start, cp.window_end AS cw_end "
        "FROM drafts d JOIN leads l ON l.id = d.lead_id JOIN campaigns cp ON cp.id = d.campaign_id "
        "WHERE d.campaign_id=:c AND d.lead_id=:l AND d.kind=:k"), {"c": campaign_id, "l": lead_id, "k": kind}).mappings().first()
    if r is None:
        return None
    m = dict(r)
    m["recipient_domain"] = domains.registrable(domains.split_email(m["recipient_email"])[1])
    return m


def check_draft(campaign_id: int, lead_id: int, kind: str, now: float, settings: AppSettings, engine: Engine | None = None) -> ClaimResult:
    """Dry run: the same rules as `check`, for a draft that has not been queued. Nothing is written."""
    engine = engine or db.get_engine()
    with engine.connect() as c:
        m = _virtual_context(c, campaign_id, lead_id, kind)
        if m is None:
            return ClaimResult(False, "missing", "That draft does not exist.")
        return _check_ctx(c, m, now, settings, engine)


def _check_ctx(c, m: dict, now: float, settings: AppSettings, engine: Engine) -> ClaimResult:
    if db.is_suppressed(m["recipient_email"], engine):
        return ClaimResult(False, "suppressed", "This address is on the suppression list.")
    if m["lead_status"] in BLOCKING_LEAD_STATUS:
        return ClaimResult(False, "lead_status", f"The lead is marked {m['lead_status'].replace('_', ' ')}.")
    if m["conf"] == "guessed" and not settings.allow_guessed_emails:
        return ClaimResult(False, "guessed", "Guessed addresses are not sent to unless you allow it in Settings.")
    draft = _approved_draft(c, m["campaign_id"], m["lead_id"], m["kind"])
    if draft is None:
        return ClaimResult(False, "not_approved", "There is no approved draft for this email (it was never approved, or it was edited after approval).")
    if draft[2] != m["body"] or draft[1] != m["subject"]:
        return ClaimResult(False, "not_approved", "The draft changed after it was queued. Approve it again before it can be sent.")
    if paused_all(c):
        return ClaimResult(False, "paused_all", "Sending is paused for all campaigns.")
    if m["campaign_state"] != "running":
        return ClaimResult(False, "campaign", f"The campaign is {m['campaign_state']}, not running.")
    if m["not_before"] is not None and now < m["not_before"]:
        return ClaimResult(False, "not_before", "This follow-up is not due yet.", retry_at=m["not_before"])
    zone = tz.infer_zone(m["recipient_email"].split("@", 1)[1], _plan_regions(c, m["run_id"])) if settings.use_recipient_timezone else None
    if not tz.in_send_window(now, m["cw_start"] or settings.window_start, m["cw_end"] or settings.window_end, settings.send_days, zone):
        where = f"in {zone.key}" if zone else "on this Mac"
        return ClaimResult(False, "window", f"Outside the sending window {where}.", retry_at=None)
    day = tz.local_day_start(now)
    today = c.execute(text(f"SELECT COUNT(*) FROM messages WHERE state IN {COUNTED} AND claimed_at >= :d"), {"d": day}).scalar_one()
    cap = effective_cap(settings, m["campaign_cap"])
    if today >= cap:
        return ClaimResult(False, "daily_cap", f"The daily limit of {cap} emails has been reached.", retry_at=day + 86400)
    per = c.execute(text(f"SELECT COUNT(*) FROM messages WHERE state IN {COUNTED} AND recipient_domain = :d AND claimed_at >= :s"),
                    {"d": m["recipient_domain"], "s": day}).scalar_one()
    if per >= settings.max_per_domain_per_day:
        return ClaimResult(False, "domain_daily_limit", f"Already {per} email(s) to {m['recipient_domain']} today (limit {settings.max_per_domain_per_day}).", retry_at=day + 86400)
    gap = settings.min_minutes_between_same_domain * 60
    last = c.execute(text(f"SELECT MAX(claimed_at) FROM messages WHERE state IN {COUNTED} AND recipient_domain = :d"), {"d": m["recipient_domain"]}).scalar()
    if last is not None and now - last < gap:
        return ClaimResult(False, "domain_gap", f"Another person at {m['recipient_domain']} was emailed recently; waiting {settings.min_minutes_between_same_domain} minutes between people.", retry_at=last + gap)
    return ClaimResult(True)


def claim(message_id: int, now: float | None, settings: AppSettings, engine: Engine | None = None) -> ClaimResult:
    """Take the message for sending: queued -> sending, atomically. The caller may send only if `ok`, and must then
    report the outcome with mark_sent / mark_failed. Nothing else ever puts a message into `sending`."""
    engine = engine or db.get_engine()
    now = time.time() if now is None else now
    pre = check(message_id, now, settings, engine)
    if not pre.ok:
        return pre
    attempt, day = uuid.uuid4().hex, tz.local_day_start(now)
    gap = settings.min_minutes_between_same_domain * 60
    with engine.begin() as c:
        m = _context(c, message_id)
        cap = effective_cap(settings, m["campaign_cap"]) if m else 0
        # The re-check lives in the UPDATE itself: SQLite runs one statement under the writer lock, so a second worker
        # evaluating the same quotas at the same moment either sees this claim or loses the race here.
        res = c.execute(text(
            "UPDATE messages SET state='sending', attempt_id=:a, claimed_at=:now WHERE id=:id AND state='queued' "
            "AND NOT EXISTS (SELECT 1 FROM suppression s WHERE s.email = messages.recipient_email) "
            "AND NOT EXISTS (SELECT 1 FROM app_state a WHERE a.key = 'pause_all' AND a.value = '1') "
            "AND EXISTS (SELECT 1 FROM drafts d WHERE d.campaign_id = messages.campaign_id AND d.lead_id = messages.lead_id AND d.kind = messages.kind "
            "AND d.status = 'approved' AND d.approved = 1 AND d.body = messages.body AND d.subject IS messages.subject) "
            f"AND (SELECT COUNT(*) FROM messages x WHERE x.state IN {COUNTED} AND x.claimed_at >= :day) < :cap "
            f"AND (SELECT COUNT(*) FROM messages x WHERE x.state IN {COUNTED} AND x.recipient_domain = messages.recipient_domain AND x.claimed_at >= :day) < :per "
            f"AND NOT EXISTS (SELECT 1 FROM messages x WHERE x.state IN {COUNTED} AND x.recipient_domain = messages.recipient_domain AND x.claimed_at > :now - :gap)"),
            {"a": attempt, "now": now, "id": message_id, "day": day, "cap": cap, "per": settings.max_per_domain_per_day, "gap": gap})
        if res.rowcount != 1:
            lost = True
        else:
            lost = False
            _trace(c, m["trace_id"] if m else None, "sending", "Claimed for sending (all send-safety checks passed)")
    if lost:
        again = check(message_id, now, settings, engine)
        return again if not again.ok else ClaimResult(False, "lost_race", "Another sender took this slot; try again shortly.")
    slog.log_event("message.claimed", trace_id=(m or {}).get("trace_id") or "", message_id=message_id, domain=(m or {}).get("recipient_domain"))
    return ClaimResult(True, attempt_id=attempt)


def resync_snapshots(campaign_id: int, engine: Engine | None = None) -> int:
    """A queued (never claimed) message follows its draft when the user edits and re-approves it: the snapshot becomes the
    CURRENTLY approved text. Only approved text is ever copied, and a message past `queued` is never touched."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(text(
            "UPDATE messages SET body = (SELECT d.body FROM drafts d WHERE d.campaign_id = messages.campaign_id AND d.lead_id = messages.lead_id AND d.kind = messages.kind AND d.status='approved' AND d.approved=1), "
            "subject = (SELECT d.subject FROM drafts d WHERE d.campaign_id = messages.campaign_id AND d.lead_id = messages.lead_id AND d.kind = messages.kind AND d.status='approved' AND d.approved=1) "
            "WHERE campaign_id=:c AND state='queued' AND EXISTS (SELECT 1 FROM drafts d WHERE d.campaign_id = messages.campaign_id AND d.lead_id = messages.lead_id AND d.kind = messages.kind "
            "AND d.status='approved' AND d.approved=1 AND (d.body IS NOT messages.body OR d.subject IS NOT messages.subject))"), {"c": campaign_id})
    return res.rowcount


def set_message_id(message_id: int, attempt_id: str, smtp_message_id: str, engine: Engine | None = None) -> bool:
    """Store the Message-ID BEFORE the SMTP call, so a message left in `sending` / `needs_confirmation` can be found in
    Gmail > Sent by its id. Only the holder of the current attempt may do this."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(text("UPDATE messages SET message_id=:mid WHERE id=:id AND state='sending' AND attempt_id=:a"),
                        {"mid": smtp_message_id, "id": message_id, "a": attempt_id})
    return bool(res.rowcount)


def release(message_id: int, attempt_id: str, note: str = "", engine: Engine | None = None) -> bool:
    """sending -> queued, for the one case where the server definitely did not get the message (login refused, no
    connection, a 4xx before DATA). The claim no longer counts against quotas."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(text("UPDATE messages SET state='queued', attempt_id=NULL, claimed_at=NULL, message_id=NULL, error=:e "
                             "WHERE id=:id AND state='sending' AND attempt_id=:a"), {"e": note[:500] or None, "id": message_id, "a": attempt_id})
        if res.rowcount:
            t = c.execute(text("SELECT trace_id FROM messages WHERE id=:i"), {"i": message_id}).scalar()
            _trace(c, t, "released", (note or "The server did not accept the message; it stays queued")[:200])
    return bool(res.rowcount)


def mark_sent(message_id: int, attempt_id: str, smtp_message_id: str, smtp_result: str = "250 OK", engine: Engine | None = None) -> bool:
    """Record a delivered hand-off. Only the holder of the current attempt may do this."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(text(
            "UPDATE messages SET state='sent', message_id=:mid, smtp_result=:r, sent_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), error=NULL "
            "WHERE id=:id AND state='sending' AND attempt_id=:a"), {"mid": smtp_message_id, "r": smtp_result, "id": message_id, "a": attempt_id})
        if res.rowcount:
            t = c.execute(text("SELECT trace_id FROM messages WHERE id=:i"), {"i": message_id}).scalar()
            _trace(c, t, "sent", f"Sent ({smtp_result})")
    return bool(res.rowcount)


def mark_failed(message_id: int, attempt_id: str, error: str, outcome_unknown: bool = False, engine: Engine | None = None) -> bool:
    """The send did not complete. `failed` means nothing reached the server (safe to retry on request);
    `outcome_unknown` means it may have (a timeout after the message was handed over): the user must confirm."""
    engine = engine or db.get_engine()
    state = "needs_confirmation" if outcome_unknown else "failed"
    with engine.begin() as c:
        res = c.execute(text("UPDATE messages SET state=:s, error=:e WHERE id=:id AND state='sending' AND attempt_id=:a"),
                        {"s": state, "e": error[:500], "id": message_id, "a": attempt_id})
        if res.rowcount:
            t = c.execute(text("SELECT trace_id FROM messages WHERE id=:i"), {"i": message_id}).scalar()
            _trace(c, t, state, error[:200])
    return bool(res.rowcount)


def recover_stale(engine: Engine | None = None, older_than_s: float | None = None, now: float | None = None) -> int:
    """Run when the worker starts (older_than_s=None: every `sending` row, since nothing can still be sending).
    A message left in `sending` may or may not have been delivered; it is never retried automatically."""
    engine = engine or db.get_engine()
    now = time.time() if now is None else now
    cutoff = None if older_than_s is None else now - older_than_s
    with engine.begin() as c:
        rows = c.execute(text("SELECT id, trace_id FROM messages WHERE state='sending'" + ("" if cutoff is None else " AND claimed_at < :c")),
                         {"c": cutoff} if cutoff is not None else {}).all()
        for mid, t in rows:
            c.execute(text("UPDATE messages SET state='needs_confirmation', error='The app stopped while this was sending. Check Gmail > Sent, then confirm.' WHERE id=:i AND state='sending'"), {"i": mid})
            _trace(c, t, "needs_confirmation", "The app stopped while this email was being sent; it will not be resent unless you say it was not sent")
    if rows:
        slog.log_event("message.recovered", count=len(rows))
    return len(rows)


def confirm(message_id: int, was_sent: bool, engine: Engine | None = None) -> bool:
    """The user's decision on a needs_confirmation message: it was sent (close it) or it was not (queue it again)."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        if was_sent:
            res = c.execute(text("UPDATE messages SET state='sent', smtp_result='confirmed by you', sent_at=COALESCE(sent_at, strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
                                 "WHERE id=:i AND state='needs_confirmation'"), {"i": message_id})
        else:
            res = c.execute(text("UPDATE messages SET state='queued', attempt_id=NULL, claimed_at=NULL, error=NULL WHERE id=:i AND state='needs_confirmation'"), {"i": message_id})
        if res.rowcount:
            t = c.execute(text("SELECT trace_id FROM messages WHERE id=:i"), {"i": message_id}).scalar()
            _trace(c, t, "confirmed", "You confirmed it was sent" if was_sent else "You said it was not sent; queued again")
    return bool(res.rowcount)


def retry_failed(message_id: int, engine: Engine | None = None) -> bool:
    """An explicit retry of a message that definitely did not go out."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(text("UPDATE messages SET state='queued', attempt_id=NULL, claimed_at=NULL, error=NULL WHERE id=:i AND state='failed'"), {"i": message_id})
    return bool(res.rowcount)


def cancel(message_id: int, reason: str = "", engine: Engine | None = None) -> bool:
    """queued -> cancelled (it can never be sent, or the user withdrew it). `reason` is shown in the UI."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(text("UPDATE messages SET state='cancelled', error=:e WHERE id=:i AND state='queued'"), {"i": message_id, "e": reason[:500] or None})
        if res.rowcount:
            t = c.execute(text("SELECT trace_id FROM messages WHERE id=:i"), {"i": message_id}).scalar()
            _trace(c, t, "cancelled", (reason or "Cancelled")[:200])
    return bool(res.rowcount)


def next_eligible(campaign_id: int, now: float, settings: AppSettings, engine: Engine | None = None, limit: int = 1) -> list[int]:
    """Queued messages of a campaign that may be claimed right now, oldest first. Never returns a message that is
    not `queued`, so nothing in sending / sent / needs_confirmation can be picked up again."""
    engine = engine or db.get_engine()
    with engine.connect() as c:
        ids = [r[0] for r in c.execute(text("SELECT id FROM messages WHERE campaign_id=:c AND state='queued' ORDER BY id"), {"c": campaign_id})]
    out: list[int] = []
    taken_domains: set[str] = set()
    for mid in ids:
        r = check(mid, now, settings, engine)
        if r.ok:
            dom = (get_message(mid, engine) or {}).get("recipient_domain")
            if dom in taken_domains:
                continue  # two people at one company are never claimed in the same pass: the gap applies between them
            taken_domains.add(dom)
            out.append(mid)
            if len(out) >= limit:
                break
    return out
