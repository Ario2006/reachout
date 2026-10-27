"""IMAP poller (milestone 5): turns new Gmail mail into `events` rows, lead statuses and suppression entries.

Folders: INBOX only. Gmail delivers replies and delivery reports (DSNs) to INBOX. "[Gmail]/All Mail" would also hold
every message we sent (the SMTP copy), so each poll would download and parse our own mail again, and it would need a
second UID sequence and UIDVALIDITY to track; the only gain is a reply that a Gmail filter skipped the inbox for,
which is rare. `tracker.FOLDERS` is the one place to add it.

Safety properties (each has a test in tests/test_inbox.py):
* idempotent: every recorded email stores its own Message-ID (or a hash of its bytes) in `events.imap_message_id`,
  which has a unique index. Re-delivery, a UIDVALIDITY reset or a crash mid-batch re-processes without creating
  anything twice; the suppression insert happens before the event insert, so a crash can not leave a recorded
  opt-out without its suppression entry.
* a weaker outcome never overwrites a stronger lead status (`tracker.LEAD_PRIORITY`).
* a login failure is stored in `imap_state.last_error`, ends the job normally and is not retried in a loop;
  scheduled polls back off for an hour after one. The Dashboard shows it as the `gmail_login` attention item.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from backend.core import db, event_types, events, jobs, services
from backend.core.jobs import JobContext
from backend.core.ports import ImapAuthError
from backend.core.tracker import EVENT_STATUS, FOLDERS, LEAD_PRIORITY, Match, Parsed, match_bounce, match_reply, parse_email

log = logging.getLogger("reachout.inbox")

LOGIN_ERROR_PREFIX = "Gmail login failed: "
LOGIN_BACKOFF_S = 3600
MAX_ROUNDS = 40
KIND_KEY = {"reply": "new_replies", "bounce": "bounces", "auto_reply": "auto_replies", "opt_out": "opt_outs"}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------ state
def _get_state(engine: Engine, folder: str) -> tuple[int | None, int]:
    with engine.connect() as c:
        row = c.execute(text("SELECT uidvalidity, last_uid FROM imap_state WHERE folder = :f"), {"f": folder}).first()
    return (row[0], row[1]) if row else (None, 0)


def _save_state(engine: Engine, folder: str, **fields: object) -> None:
    cols = list(fields)
    sets = ", ".join(f"{k} = excluded.{k}" for k in cols)
    with engine.begin() as c:
        c.execute(text(f"INSERT INTO imap_state (folder, {', '.join(cols)}) VALUES (:folder, {', '.join(':' + k for k in cols)}) "
                       f"ON CONFLICT(folder) DO UPDATE SET {sets}"), {"folder": folder, **fields})


def active_check_job(engine: Engine) -> dict | None:
    found = jobs.list_jobs(kind="inbox_check", active_only=True, limit=1, engine=engine)
    return found[0] if found else None


def status(engine: Engine) -> dict:
    """Backs GET /inbox/status."""
    with engine.connect() as c:
        row = c.execute(text("SELECT MAX(last_checked_at) FROM imap_state")).scalar()
        err = c.execute(text("SELECT last_error FROM imap_state WHERE last_error IS NOT NULL "
                             "ORDER BY last_checked_at DESC LIMIT 1")).scalar()
    minutes = db.load_settings(engine).imap_poll_minutes
    return {"last_checked_at": row, "next_check_at": (row + minutes * 60) if row else None, "last_error": err,
            "checking": active_check_job(engine) is not None}


# ------------------------------------------------------------------------------------------------ one email
def _event_key(p: Parsed, raw: bytes) -> str:
    return p.message_id or "sha256:" + hashlib.sha256(raw).hexdigest()


def process_email(engine: Engine, raw: bytes, own_address: str, now: float) -> str | None:
    """Record one incoming email. Returns the event type recorded, or None when it was ignored, unrelated or already
    recorded."""
    p = parse_email(raw, own_address)
    if p.kind == "ignore":
        return None
    key = _event_key(p, raw)
    with engine.connect() as c:
        if c.execute(text("SELECT 1 FROM events WHERE imap_message_id = :k"), {"k": key}).first():
            return None
        match: Match | None = match_bounce(c, p) if p.kind == "bounce" else match_reply(c, p)
    if match is None:
        return None
    etype = p.kind
    hard = p.hard_bounce
    to_suppress: list[tuple[str, str]] = []
    if hard:
        to_suppress.append((match.lead_email, f"hard bounce {p.status_code}"))
        if match.how == "header" and p.failed_recipients:
            to_suppress.append((p.failed_recipients[0], f"hard bounce {p.status_code}"))
    elif etype == "opt_out":
        to_suppress.append((match.lead_email, "opt-out reply (automatic)"))
        if p.from_addr and p.from_addr != match.lead_email:
            to_suppress.append((p.from_addr, "opt-out reply (automatic)"))
    for addr, reason in to_suppress:  # before the event row: see module docstring
        db.add_suppression(addr, reason, engine)

    when = p.occurred_at if p.occurred_at and p.occurred_at <= _iso(now + 86400) else _iso(now)
    new_status = "bounced" if hard else EVENT_STATUS.get(etype)
    try:
        with engine.begin() as c:
            res = c.execute(text(
                "INSERT INTO events (lead_id, message_ref, type, snippet, occurred_at, imap_message_id, from_addr, subject, status_code) "
                "VALUES (:l, :m, :t, :s, :at, :k, :f, :sub, :sc)"),
                {"l": match.lead_id, "m": match.message_ref, "t": etype, "s": p.snippet, "at": when, "k": key,
                 "f": p.from_addr, "sub": p.subject[:300], "sc": p.status_code})
            event_id = int(res.lastrowid)
            if new_status:
                weaker = [s for s, r in LEAD_PRIORITY.items() if r < LEAD_PRIORITY[new_status]]
                marks = ", ".join(f":w{i}" for i in range(len(weaker)))
                c.execute(text(f"UPDATE leads SET status = :s WHERE id = :i AND status IN ({marks})"),
                          {"s": new_status, "i": match.lead_id, **{f"w{i}": s for i, s in enumerate(weaker)}})
            c.execute(text("UPDATE leads SET last_activity_at = :t, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                           "WHERE id = :i AND (last_activity_at IS NULL OR last_activity_at < :t)"),
                      {"t": when, "i": match.lead_id})
    except IntegrityError:  # recorded concurrently by another check: the unique index did its job
        return None
    event_types.publish("inbox", event_types.InboxEvent(lead_id=match.lead_id, event_id=event_id, type=etype), engine)
    events.emit("lead.updated", {"id": match.lead_id}, engine)
    return etype


# ------------------------------------------------------------------------------------------------ the job
def _check_folder(ctx: JobContext, imap, folder: str, own: str, now: float, totals: dict) -> None:
    engine = ctx.engine
    validity, after = _get_state(engine, folder)
    reset = False
    for _ in range(MAX_ROUNDS):
        res = imap.fetch_new(folder, after)
        if validity is not None and res.uidvalidity != validity and not reset:
            log.warning("UIDVALIDITY of %s changed (%s -> %s): re-reading the folder", folder, validity, res.uidvalidity)
            reset, validity, after = True, res.uidvalidity, 0
            _save_state(engine, folder, uidvalidity=validity, last_uid=0)
            continue
        if validity is None:
            validity = res.uidvalidity
        if not res.messages:
            break
        for m in res.messages:
            totals["checked"] += 1
            try:
                kind = process_email(engine, m.raw, own, now)
            except Exception:  # noqa: BLE001 - one bad email must not stall the folder
                log.exception("could not process uid %s in %s", m.uid, folder)
                totals["errors"] += 1
                kind = None
            if kind:
                totals[KIND_KEY[kind]] += 1
            after = max(after, m.uid)
        _save_state(engine, folder, uidvalidity=validity, last_uid=after)
        ctx.progress(totals["checked"], None, f"Checked {totals['checked']} email(s)")
        ctx.raise_if_cancelled()
    _save_state(engine, folder, uidvalidity=validity, last_uid=after)


def run_inbox_check(ctx: JobContext, payload: dict) -> dict:
    """Job `inbox_check`. Result: {"new_replies": n, "bounces": n, "auto_replies": n, "opt_outs": n, ...}."""
    engine = ctx.engine
    svc = services.get_services()
    now = svc.clock.now()
    own = db.load_settings(engine).gmail_address
    totals: dict = {"new_replies": 0, "bounces": 0, "auto_replies": 0, "opt_outs": 0, "checked": 0, "errors": 0}
    ctx.progress(0, None, "Checking Gmail", force=True)
    error: str | None = None
    imap = None
    try:
        imap = svc.imap()
        for folder in FOLDERS:
            _check_folder(ctx, imap, folder, own, now, totals)
    except ImapAuthError as e:
        error = LOGIN_ERROR_PREFIX + str(e)
        log.warning("inbox check: %s", error)
    except jobs.JobCancelled:
        raise
    except Exception as e:  # noqa: BLE001 - network trouble is recorded, the next poll retries
        error = f"Could not check Gmail: {e}"
        log.warning("inbox check failed: %s", e)
    finally:
        if imap is not None:
            try:
                imap.close()
            except Exception:  # noqa: BLE001
                pass
    for folder in FOLDERS:
        _save_state(engine, folder, last_checked_at=now, last_error=error)
    if error:
        totals["error"] = error
    ctx.progress(totals["checked"], totals["checked"], "Gmail check failed" if error else "Done", force=True)
    return totals


# ------------------------------------------------------------------------------------------------ scheduling
def scheduled_tick(engine: Engine, now: float | None = None) -> int | None:
    """Queues an `inbox_check` job unless one is active, no account is set up, or the last login was rejected less
    than an hour ago (retrying a rejected App Password in a tight loop gets the account locked)."""
    now = time.time() if now is None else now
    if not db.load_settings(engine).gmail_address:
        return None
    if active_check_job(engine) is not None:
        return None
    with engine.connect() as c:
        row = c.execute(text("SELECT last_checked_at, last_error FROM imap_state "
                             "ORDER BY last_checked_at DESC LIMIT 1")).first()
    if row and row[1] and row[1].startswith(LOGIN_ERROR_PREFIX) and row[0] and now - row[0] < LOGIN_BACKOFF_S:
        return None
    return jobs.create_job("inbox_check", {}, engine)["id"]


def register(sched: BaseScheduler, engine: Engine) -> None:
    """Adds the poll job (every `imap_poll_minutes`, read when the worker starts) to the worker's scheduler."""
    minutes = db.load_settings(engine).imap_poll_minutes
    sched.add_job(lambda: scheduled_tick(engine), "interval", minutes=minutes, id="inbox_poll", max_instances=1,
                  coalesce=True)
