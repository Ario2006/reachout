"""Campaign records (create, read, update, delete) and the run controls the API exposes: readiness, start, pause,
resume, pause-all, test send, message list, stats. The sending itself lives in `backend.core.sender`.

Campaign states: draft -> running <-> paused -> done. `paused_by` says who stopped it (user, bounce_rate, smtp_login,
shutdown, all) and `pause_reason` is the sentence the UI shows. Only the sender worker ever claims messages."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Engine, text

from backend.core import db, event_types, footer, outbox, secrets, services, tz
from backend.core.config import HARD_DAILY_CEILING
from backend.core.mailer import new_message_id
from backend.core.models import risk_warnings
from backend.core.ports import OutgoingEmail

ATTACHMENT_WARN_BYTES = 2 * 1024 * 1024
EDITABLE_FIELDS = ("name", "template_id", "mode", "attachment_path", "daily_cap", "window_start", "window_end",
                   "delay_min_s", "delay_max_s", "followups_enabled", "followups_require_approval")


def _counts(c, campaign_id: int) -> dict:
    d = {r[0]: r[1] for r in c.execute(text("SELECT status, COUNT(*) FROM drafts WHERE campaign_id=:i AND kind='initial' GROUP BY status"), {"i": campaign_id})}
    m = {r[0]: r[1] for r in c.execute(text("SELECT state, COUNT(*) FROM messages WHERE campaign_id=:i GROUP BY state"), {"i": campaign_id})}
    return {"drafts_pending": d.get("pending", 0), "drafts_needs_review": d.get("needs_review", 0), "drafts_approved": d.get("approved", 0),
            "queued": m.get("queued", 0), "sending": m.get("sending", 0), "sent": m.get("sent", 0), "failed": m.get("failed", 0),
            "needs_confirmation": m.get("needs_confirmation", 0)}


def _warnings(row: dict, engine: Engine) -> list[str]:
    out = []
    path = row.get("attachment_path")
    if path:
        try:
            if Path(path).stat().st_size > ATTACHMENT_WARN_BYTES:
                out.append("The attachment is over 2 MB; large files make cold emails look suspicious and may be filtered.")
        except OSError:
            out.append("The attachment file cannot be read.")
    s = db.load_settings(engine).model_copy(update={"daily_cap": row["daily_cap"]})
    out += [w for w in risk_warnings(s) if w.startswith("Daily cap")]
    return out


def _out(c, r, engine: Engine) -> dict:
    row = dict(r._mapping)
    row["followups_enabled"] = bool(row["followups_enabled"])
    row["followups_require_approval"] = bool(row["followups_require_approval"])
    row["followup_days"] = json.loads(row.pop("followup_days_json") or "[4, 9]")
    row["counts"] = _counts(c, row["id"])
    row["warnings"] = _warnings(row, engine)
    return row


def get(campaign_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        r = c.execute(text("SELECT * FROM campaigns WHERE id=:i"), {"i": campaign_id}).first()
        return _out(c, r, engine) if r else None


def list_all(engine: Engine | None = None) -> list[dict]:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        return [_out(c, r, engine) for r in c.execute(text("SELECT * FROM campaigns ORDER BY id DESC"))]


def _cols(data: dict) -> dict:
    cols = {k: data[k] for k in EDITABLE_FIELDS if k in data}
    for k in ("followups_enabled", "followups_require_approval"):
        if k in cols:
            cols[k] = int(bool(cols[k]))
    if "followup_days" in data:
        cols["followup_days_json"] = json.dumps(list(data["followup_days"]))
    return cols


def create(data: dict, engine: Engine | None = None) -> dict:
    engine = engine or db.get_engine()
    cols = _cols(data)
    with engine.begin() as c:
        cid = c.execute(text(f"INSERT INTO campaigns ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)})"), cols).lastrowid
    return get(cid, engine)  # type: ignore[return-value]


class CampaignLocked(Exception):
    """Settings of a running or finished campaign cannot be edited; pause it first."""


def update(campaign_id: int, data: dict, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    cur = get(campaign_id, engine)
    if cur is None:
        return None
    if cur["state"] in ("running", "done"):
        raise CampaignLocked(f"The campaign is {cur['state']}; pause it to edit its settings.")
    cols = _cols(data)
    if cols:
        with engine.begin() as c:
            c.execute(text(f"UPDATE campaigns SET {', '.join(f'{k}=:{k}' for k in cols)} WHERE id=:id"), {**cols, "id": campaign_id})
    return get(campaign_id, engine)


def delete(campaign_id: int, engine: Engine | None = None) -> bool:
    """Only a campaign that never sent anything can go; history is kept otherwise."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        if c.execute(text("SELECT COUNT(*) FROM messages WHERE campaign_id=:i"), {"i": campaign_id}).scalar_one():
            raise CampaignLocked("This campaign has sent or queued emails and cannot be deleted.")
        return c.execute(text("DELETE FROM campaigns WHERE id=:i"), {"i": campaign_id}).rowcount > 0


# ================================================================== run controls
class ControlRefused(Exception):
    """A run control that cannot happen right now (the API answers 409 with this sentence)."""


def _row(c, campaign_id: int):
    return c.execute(text("SELECT id, state, paused_by, pause_reason, next_send_at, mode, attachment_path, name, followups_enabled FROM campaigns WHERE id=:i"), {"i": campaign_id}).mappings().first()


def publish_campaign(campaign_id: int, engine: Engine) -> None:
    with engine.connect() as c:
        r = _row(c, campaign_id)
    if r:
        event_types.publish("campaign", event_types.CampaignEvent(campaign_id=campaign_id, state=r["state"], paused_by=r["paused_by"],
                                                                  pause_reason=r["pause_reason"], next_send_at=r["next_send_at"]), engine)


def publish_sending(engine: Engine) -> None:
    st = sending_status(engine)
    event_types.publish("sending", event_types.SendingEvent(paused_all=st["paused_all"], sent_today=st["sent_today"], cap=st["cap"]), engine)


def _bounce_where() -> str:
    return ("e.type = 'bounce' AND (e.message_ref IN (SELECT id FROM messages WHERE campaign_id = :c) "
            "OR (e.message_ref IS NULL AND e.lead_id IN (SELECT lead_id FROM messages WHERE campaign_id = :c AND state = 'sent')))")


def bounce_stats(campaign_id: int, engine: Engine | None = None) -> tuple[int, int]:
    """(emails sent, people who bounced) for the campaign."""
    engine = engine or db.get_engine()
    with engine.connect() as c:
        sent = c.execute(text("SELECT COUNT(*) FROM messages WHERE campaign_id=:c AND state='sent'"), {"c": campaign_id}).scalar_one()
        bounced = c.execute(text(f"SELECT COUNT(DISTINCT e.lead_id) FROM events e WHERE {_bounce_where()}"), {"c": campaign_id}).scalar_one()
    return int(sent), int(bounced)


def _set_state(campaign_id: int, engine: Engine, state: str, *, only_from: tuple[str, ...], paused_by: str | None = None,
               reason: str | None = None, extra: str = "") -> bool:
    marks = ", ".join(f":f{i}" for i in range(len(only_from)))
    params = {"s": state, "pb": paused_by, "r": reason, "id": campaign_id, **{f"f{i}": v for i, v in enumerate(only_from)}}
    with engine.begin() as c:
        res = c.execute(text(f"UPDATE campaigns SET state=:s, paused_by=:pb, pause_reason=:r{extra} WHERE id=:id AND state IN ({marks})"), params)
    if res.rowcount:
        publish_campaign(campaign_id, engine)
    return bool(res.rowcount)


def pause(campaign_id: int, by: str = "user", reason: str | None = None, engine: Engine | None = None) -> dict | None:
    """running -> paused. Anything else is left alone (pausing twice is harmless)."""
    engine = engine or db.get_engine()
    if get(campaign_id, engine) is None:
        return None
    _set_state(campaign_id, engine, "paused", only_from=("running",), paused_by=by,
               reason=reason or {"user": "You paused this campaign."}.get(by, "Paused."))
    return get(campaign_id, engine)


def pause_all_running(by: str, reason: str, engine: Engine) -> int:
    with engine.connect() as c:
        ids = [r[0] for r in c.execute(text("SELECT id FROM campaigns WHERE state='running'"))]
    for i in ids:
        _set_state(i, engine, "paused", only_from=("running",), paused_by=by, reason=reason)
    return len(ids)


def pause_for_shutdown(engine: Engine | None = None) -> int:
    return pause_all_running("shutdown", "The app was closed while this campaign was sending. It continues when the app is open again.", engine or db.get_engine())


def resume_after_restart(engine: Engine | None = None) -> int:
    """Campaigns the app itself paused on shutdown continue; anything paused by the user, the bounce guard, a login
    problem or pause-all stays paused."""
    engine = engine or db.get_engine()
    with engine.connect() as c:
        ids = [r[0] for r in c.execute(text("SELECT id FROM campaigns WHERE state='paused' AND paused_by='shutdown'"))]
    for i in ids:
        _set_state(i, engine, "running", only_from=("paused",))
    return len(ids)


def enqueue_approved(campaign_id: int, engine: Engine) -> int:
    """Queue every approved initial draft that is not queued yet. Idempotent. Returns how many were newly queued."""
    with engine.connect() as c:
        rows = c.execute(text("SELECT lead_id FROM drafts WHERE campaign_id=:c AND kind='initial' AND status='approved' AND approved=1 ORDER BY id"), {"c": campaign_id}).all()
    n = 0
    for (lead_id,) in rows:
        mid, created = outbox.enqueue(lead_id, campaign_id, "initial", "", engine)
        if created:
            n += 1
            with engine.begin() as c:
                c.execute(text("UPDATE leads SET status='queued' WHERE id=:i AND status IN ('new','drafted','approved')"), {"i": lead_id})
            m = outbox.get_message(mid, engine) or {}
            event_types.publish("message", event_types.MessageEvent(campaign_id=campaign_id, lead_id=lead_id, message_id=mid, kind="initial", state="queued"), engine)
            del m
    return n


def _gmail_ready(engine: Engine) -> tuple[bool, str]:
    s = db.load_settings(engine)
    if not s.gmail_address:
        return False, "No Gmail address is saved. Add it in Settings."
    if not secrets.has_app_password(s.gmail_address):
        return False, "No App Password is saved for this address. Add it in Settings."
    return True, f"Sending as {s.gmail_address}"


def readiness(campaign_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    cp = get(campaign_id, engine)
    if cp is None:
        return None
    gm_ok, gm_detail = _gmail_ready(engine)
    approved = cp["counts"]["drafts_approved"]
    path = cp.get("attachment_path")
    att_ok, att_blocking, att_detail = True, False, "No attachment."
    if path:
        try:
            size = Path(path).expanduser().stat().st_size
            att_ok = size <= ATTACHMENT_WARN_BYTES
            att_detail = f"{size / 1024 / 1024:.1f} MB" + ("" if att_ok else ": over 2 MB, large files make cold emails look suspicious.")
        except OSError:
            att_ok, att_blocking, att_detail = False, True, "The attachment file cannot be read."
    nc = cp["counts"]["needs_confirmation"]
    items = [
        dict(id="gmail", label="Gmail is connected", ok=gm_ok, detail=gm_detail, blocking=True),
        dict(id="approved_drafts", label="Drafts are approved", ok=approved > 0, detail=f"{approved} approved draft(s)." if approved else "Approve at least one draft in Compose.", blocking=True),
        dict(id="attachment", label="Attachment is fine", ok=att_ok, detail=att_detail, blocking=att_blocking),
        dict(id="test_sent", label="A test email was sent to you", ok=bool(cp["test_sent_at"]), detail="Test sent." if cp["test_sent_at"] else "Send a test to yourself first (recommended).", blocking=False),
        dict(id="needs_confirmation", label="No emails waiting for your confirmation", ok=nc == 0, detail="Nothing to confirm." if nc == 0 else f"{nc} email(s) need you to confirm whether they were sent.", blocking=False),
    ]
    return {"items": items, "ready": all(i["ok"] for i in items if i["blocking"])}


def start(campaign_id: int, engine: Engine | None = None) -> dict | None:
    """draft -> running: queues the approved drafts (their text is snapshotted) and lets the sender work."""
    engine = engine or db.get_engine()
    cp = get(campaign_id, engine)
    if cp is None:
        return None
    if cp["state"] != "draft":
        raise ControlRefused({"running": "The campaign is already running.", "paused": "The campaign is paused; use Resume.", "done": "The campaign is finished."}[cp["state"]])
    rd = readiness(campaign_id, engine) or {"items": []}
    for it in rd["items"]:
        if it["blocking"] and not it["ok"]:
            raise ControlRefused(it["detail"] if it["id"] != "gmail" else f"Gmail is not set up. {it['detail']}")
    if outbox.is_paused_all(engine):
        raise ControlRefused("Sending is paused for all campaigns. Turn it back on first.")
    enqueue_approved(campaign_id, engine)
    _set_state(campaign_id, engine, "running", only_from=("draft",), extra=", started_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), next_send_at=NULL")
    publish_sending(engine)
    return get(campaign_id, engine)


def resume(campaign_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    cp = get(campaign_id, engine)
    if cp is None:
        return None
    if cp["state"] != "paused":
        raise ControlRefused("Only a paused campaign can be resumed.")
    if outbox.is_paused_all(engine):
        raise ControlRefused("Sending is paused for all campaigns. Turn it back on first.")
    if cp["paused_by"] == "smtp_login":
        ok, detail = _gmail_ready(engine)
        if not ok:
            raise ControlRefused(detail)
    if cp["paused_by"] == "bounce_rate":  # the user has seen the warning: only NEW bounces can trip the guard again
        _, bounced = bounce_stats(campaign_id, engine)
        _set_app_state(f"bounce_ack:{campaign_id}", str(bounced), engine)
    enqueue_approved(campaign_id, engine)
    _set_state(campaign_id, engine, "running", only_from=("paused",))  # next_send_at is kept: pausing never shortens the spacing
    publish_sending(engine)
    return get(campaign_id, engine)


def _set_app_state(key: str, value: str, engine: Engine) -> None:
    with engine.begin() as c:
        c.execute(text("INSERT INTO app_state (key, value) VALUES (:k, :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')"), {"k": key, "v": value})


def get_app_state(key: str, engine: Engine) -> str | None:
    with engine.connect() as c:
        return c.execute(text("SELECT value FROM app_state WHERE key=:k"), {"k": key}).scalar()


def set_app_state(key: str, value: str, engine: Engine) -> None:
    _set_app_state(key, value, engine)


def pause_all(engine: Engine | None = None) -> dict:
    engine = engine or db.get_engine()
    outbox.set_paused_all(True, engine)
    pause_all_running("all", "Sending was paused for all campaigns.", engine)
    publish_sending(engine)
    return sending_status(engine)


def resume_all(engine: Engine | None = None) -> dict:
    engine = engine or db.get_engine()
    outbox.set_paused_all(False, engine)
    with engine.connect() as c:
        ids = [r[0] for r in c.execute(text("SELECT id FROM campaigns WHERE state='paused' AND paused_by='all'"))]
    for i in ids:
        _set_state(i, engine, "running", only_from=("paused",))
    publish_sending(engine)
    return sending_status(engine)


def reopen_if_done(campaign_id: int, engine: Engine | None = None) -> bool:
    """done -> running. For anything that adds work to a finished campaign (the user re-queued an email, the follow-up
    task scheduled a follow-up): outbox refuses to send for a campaign that is not running."""
    engine = engine or db.get_engine()
    return _set_state(campaign_id, engine, "running", only_from=("done",), extra=", finished_at=NULL")


def followups_pending(campaign_id: int, now: float, engine: Engine | None = None) -> bool:
    """True while a follow-up may still be scheduled for this campaign, so the sender must not mark it done: follow-ups
    are enabled and some person who got the first email (and is not suppressed, replied, bounced, opted out or closed)
    has had fewer follow-ups than the plan, and the last follow-up window (plus a day) has not passed."""
    engine = engine or db.get_engine()
    with engine.connect() as c:
        cp = c.execute(text("SELECT followups_enabled, followup_days_json FROM campaigns WHERE id=:i"), {"i": campaign_id}).first()
        if cp is None or not cp[0]:
            return False
        days = [int(d) for d in json.loads(cp[1] or "[]")]
        if not days:
            return False
        rows = c.execute(text(
            "SELECT m.recipient_email, m.claimed_at, l.status, (SELECT COUNT(*) FROM messages f WHERE f.campaign_id = m.campaign_id AND f.lead_id = m.lead_id "
            "AND f.kind != 'initial' AND f.state IN ('sent','needs_confirmation','failed','cancelled')) "
            "FROM messages m JOIN leads l ON l.id = m.lead_id WHERE m.campaign_id=:c AND m.kind='initial' AND m.state='sent'"), {"c": campaign_id}).all()
    horizon = (max(days) + 1) * 86400
    for email, claimed_at, status, handled in rows:
        if status in outbox.BLOCKING_LEAD_STATUS or handled >= len(days) or claimed_at is None or now > claimed_at + horizon:
            continue
        if db.is_suppressed(email, engine):
            continue
        return True
    return False


def sent_today(engine: Engine, now: float | None = None) -> int:
    import time as _t
    day = tz.local_day_start(_t.time() if now is None else now)
    with engine.connect() as c:
        return int(c.execute(text(f"SELECT COUNT(*) FROM messages WHERE state IN {outbox.COUNTED} AND claimed_at >= :d"), {"d": day}).scalar_one())


def sending_status(engine: Engine | None = None) -> dict:
    engine = engine or db.get_engine()
    s = db.load_settings(engine)
    now = services.get_services().clock.now()
    alive, _, _ = db.worker_status(engine=engine)
    with engine.connect() as c:
        in_flight = c.execute(text("SELECT COUNT(*) FROM messages WHERE state='sending'")).scalar_one()
        nxt = c.execute(text("SELECT MIN(next_send_at) FROM campaigns WHERE state='running' AND next_send_at IS NOT NULL")).scalar()
        caff = c.execute(text("SELECT value FROM app_state WHERE key='caffeinate_active'")).scalar() == "1"
        paused = outbox.paused_all(c)
    return {"paused_all": paused, "sent_today": sent_today(engine, now), "cap": max(0, min(s.daily_cap, HARD_DAILY_CEILING)), "in_flight": int(in_flight),
            "next_send_at": nxt, "worker_alive": alive, "caffeinate_active": bool(caff and alive)}


def stats(campaign_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    cp = get(campaign_id, engine)
    if cp is None:
        return None
    s = db.load_settings(engine)
    sent, bounced = bounce_stats(campaign_id, engine)
    with engine.connect() as c:
        replied = c.execute(text("SELECT COUNT(DISTINCT e.lead_id) FROM events e WHERE e.type='reply' AND e.lead_id IN (SELECT lead_id FROM messages WHERE campaign_id=:c)"), {"c": campaign_id}).scalar_one()
    cnt = cp["counts"]
    return {"approved": cnt["drafts_approved"], "queued": cnt["queued"], "sent": sent, "failed": cnt["failed"], "needs_confirmation": cnt["needs_confirmation"],
            "bounced": bounced, "replied": int(replied), "sent_today": sent_today(engine, services.get_services().clock.now()),
            "daily_cap_effective": outbox.effective_cap(s, cp["daily_cap"]),
            "bounce_rate": round(bounced / sent * 100, 1) if sent >= s.bounce_pause_min_sends else None, "next_send_at": cp["next_send_at"]}


def list_messages(campaign_id: int, state: str | None = None, engine: Engine | None = None) -> list[dict]:
    engine = engine or db.get_engine()
    sql = ("SELECT m.id, m.campaign_id, m.lead_id, l.name AS lead_name, m.recipient_email, m.kind, m.state, m.subject, m.sent_at, m.error "
           "FROM messages m JOIN leads l ON l.id = m.lead_id WHERE m.campaign_id=:c" + (" AND m.state=:s" if state else "") + " ORDER BY m.id")
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text(sql), {"c": campaign_id, "s": state}).mappings()]


def message_out(message_id: int, engine: Engine) -> dict | None:
    with engine.connect() as c:
        r = c.execute(text("SELECT m.id, m.campaign_id, m.lead_id, l.name AS lead_name, m.recipient_email, m.kind, m.state, m.subject, m.sent_at, m.error "
                           "FROM messages m JOIN leads l ON l.id = m.lead_id WHERE m.id=:i"), {"i": message_id}).mappings().first()
    return dict(r) if r else None


def confirm_message(message_id: int, was_sent: bool, engine: Engine | None = None) -> dict | None:
    """The user's word on a needs_confirmation email. Never resends by itself; 'not sent' queues it for the normal checks."""
    engine = engine or db.get_engine()
    m = outbox.get_message(message_id, engine)
    if m is None:
        return None
    if not outbox.confirm(message_id, was_sent, engine):
        raise ControlRefused("Only an email that is waiting for confirmation can be confirmed.")
    if was_sent:
        with engine.begin() as c:
            c.execute(text("UPDATE leads SET status='sent' WHERE id=:i AND status IN ('new','drafted','approved','queued')"), {"i": m["lead_id"]})
    else:
        reopen_if_done(m["campaign_id"], engine)
    event_types.publish("message", event_types.MessageEvent(campaign_id=m["campaign_id"], lead_id=m["lead_id"], message_id=message_id, kind=m["kind"],
                                                            state="sent" if was_sent else "queued"), engine)
    return message_out(message_id, engine)


def retry_message(message_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    m = outbox.get_message(message_id, engine)
    if m is None:
        return None
    if not outbox.retry_failed(message_id, engine):
        raise ControlRefused("Only an email that failed (the server never accepted it) can be retried.")
    reopen_if_done(m["campaign_id"], engine)
    event_types.publish("message", event_types.MessageEvent(campaign_id=m["campaign_id"], lead_id=m["lead_id"], message_id=message_id, kind=m["kind"], state="queued"), engine)
    return message_out(message_id, engine)


def test_send(campaign_id: int, engine: Engine | None = None) -> dict | None:
    """Send the first approved draft to the user's own address through the real mailer, footer included.
    Not recorded as a message: the person is never contacted and the one-email-per-person rule is untouched."""
    from backend.core.ports import MailerError

    engine = engine or db.get_engine()
    cp = get(campaign_id, engine)
    if cp is None:
        return None
    s = db.load_settings(engine)
    if not s.gmail_address:
        raise ControlRefused("No Gmail address is saved. Add it in Settings first.")
    with engine.connect() as c:
        d = c.execute(text("SELECT subject, body FROM drafts WHERE campaign_id=:c AND kind='initial' AND status='approved' AND approved=1 ORDER BY id LIMIT 1"), {"c": campaign_id}).first()
    if d is None:
        raise ControlRefused("There is no approved draft to test with. Approve one in Compose.")
    mid = new_message_id(s.gmail_address)
    email = OutgoingEmail(from_addr=s.gmail_address, from_name=s.display_name, to_addr=s.gmail_address, subject=f"[Test] {d[0]}",
                          body=footer.ensure_footer(d[1], s, cp["mode"]), message_id=mid, attachment_path=cp.get("attachment_path") or None)
    mailer = None
    try:
        mailer = services.get_services().mailer()  # may raise SmtpAuthError when the App Password is missing
        mailer.send(email)
    except MailerError as e:
        raise ControlRefused(f"The test email was not sent: {e}") from e
    finally:
        if mailer is not None:
            mailer.close()
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET test_sent_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=:i"), {"i": campaign_id})
    return {"to": s.gmail_address, "message_id": mid}
