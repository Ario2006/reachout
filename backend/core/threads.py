"""Inbox conversations grouped per lead, and the manual status actions (milestone 5).

A thread is everything we know about one lead: the emails we sent (`messages` with `sent_at`) and what came back
(`events`: reply, opt_out, auto_reply, bounce). "Unread" means an incoming event with `read_at IS NULL`; opening a
thread (`get_thread`) marks them read after returning their previous state, so the UI can still highlight them once.
"""
from __future__ import annotations

from urllib.parse import quote

from sqlalchemy import Engine, text

from backend.core import db, events
from backend.core.tracker import normalize_message_id

OUTCOMES = ("interested", "not_interested", "meeting_booked")
FILTERS = ("all", "unread", "replied", "bounced", "opt_out", "auto_reply", "sent")
SNIPPET_LEN = 140

# Statuses the system sets itself from the pipeline or from Gmail. A person can not pick them.
SYSTEM_ONLY = {"new", "drafted", "approved", "queued", "auto_reply", "bounced"}
# What a suppressed address may be set to: never anything that implies we may write to it again.
SUPPRESSED_OK = {"opted_out", "bounced", "closed"}
TERMINAL_FACTS = {"bounced", "opted_out"}


class StatusRefused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


_ITEMS = """
SELECT lead_id, kind, at, txt, unread, ord FROM (
  SELECT lead_id, 'sent' AS kind, sent_at AS at, COALESCE(body, subject) AS txt, 0 AS unread, 0 AS ord
    FROM messages WHERE sent_at IS NOT NULL
  UNION ALL
  SELECT lead_id, type, occurred_at, snippet, (read_at IS NULL), 1 FROM events WHERE lead_id IS NOT NULL
)
"""

_SUMMARY = f"""
WITH items AS ({_ITEMS}),
ranked AS (
  SELECT lead_id, kind, at, txt,
         ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY at DESC, ord DESC) AS rn,
         SUM(unread) OVER (PARTITION BY lead_id) AS unread_n,
         SUM(kind = 'reply') OVER (PARTITION BY lead_id) AS n_reply,
         SUM(kind = 'bounce') OVER (PARTITION BY lead_id) AS n_bounce,
         SUM(kind = 'opt_out') OVER (PARTITION BY lead_id) AS n_opt,
         SUM(kind = 'auto_reply') OVER (PARTITION BY lead_id) AS n_auto
  FROM items)
SELECT l.id, l.name, l.email, l.company, l.role, l.email_confidence, l.status, l.outcome,
       r.kind AS last_kind, r.at AS last_at, r.txt AS txt, r.unread_n, r.n_reply, r.n_bounce, r.n_opt, r.n_auto,
       EXISTS (SELECT 1 FROM events e WHERE e.lead_id = l.id AND e.type = 'opt_out' AND e.confirmed_at IS NULL) AS to_confirm
FROM ranked r JOIN leads l ON l.id = r.lead_id
WHERE r.rn = 1
"""

_FILTER_SQL = {
    "all": "", "unread": " AND unread_n > 0", "replied": " AND n_reply > 0", "bounced": " AND n_bounce > 0",
    "opt_out": " AND n_opt > 0", "auto_reply": " AND n_auto > 0",
    "sent": " AND n_reply = 0 AND n_bounce = 0 AND n_opt = 0 AND n_auto = 0",
}


def _lead_out(row) -> dict:
    return {"id": row["id"], "name": row["name"], "email": row["email"], "company": row["company"],
            "role": row["role"], "email_confidence": row["email_confidence"], "status": row["status"],
            "outcome": row["outcome"]}


def _short(t: str | None) -> str | None:
    if not t:
        return None
    t = " ".join(t.split())
    return t if len(t) <= SNIPPET_LEN else t[: SNIPPET_LEN - 1] + "…"


def list_threads(engine: Engine, filter: str = "all", q: str = "", campaign_id: int | None = None,
                 offset: int = 0, limit: int = 100) -> dict:
    where = _FILTER_SQL.get(filter, "")
    params: dict = {}
    q = q.strip()
    if q:
        params["q"] = f"%{q.lower().replace('%', '').replace('_', '')}%"
        where += (" AND (lower(COALESCE(t.name,'')) LIKE :q OR t.email LIKE :q OR lower(COALESCE(t.company,'')) LIKE :q"
                  " OR EXISTS (SELECT 1 FROM events e WHERE e.lead_id = t.id AND (lower(COALESCE(e.snippet,'')) LIKE :q"
                  " OR lower(COALESCE(e.subject,'')) LIKE :q))"
                  " OR EXISTS (SELECT 1 FROM messages m WHERE m.lead_id = t.id AND lower(COALESCE(m.subject,'')) LIKE :q))")
    if campaign_id is not None:
        params["cid"] = campaign_id
        where += " AND EXISTS (SELECT 1 FROM messages m WHERE m.lead_id = t.id AND m.campaign_id = :cid)"
    base = f"SELECT * FROM ({_SUMMARY}) AS t WHERE 1=1{where}"
    with engine.connect() as c:
        rows = c.execute(text(f"{base} ORDER BY last_at DESC, id DESC LIMIT :lim OFFSET :off"),
                         {**params, "lim": max(1, min(limit, 500)), "off": max(0, offset)}).mappings().all()
        total = c.execute(text(f"SELECT COUNT(*) FROM ({base})"), params).scalar_one()
        unread_total = c.execute(text("SELECT COUNT(*) FROM events WHERE lead_id IS NOT NULL AND read_at IS NULL")).scalar_one()
    items = [{"lead": _lead_out(r), "last_activity_at": r["last_at"], "last_kind": r["last_kind"],
              "snippet": _short(r["txt"]), "unread": int(r["unread_n"] or 0), "opt_out_to_confirm": bool(r["to_confirm"])}
             for r in rows]
    return {"items": items, "total": total, "unread_total": unread_total}


def gmail_url(engine: Engine, message_ids: list[str]) -> str | None:
    """A Gmail search link for the thread by Message-ID. Holds no secret: just the address (for the right account)
    and the public Message-ID."""
    if not message_ids:
        return None
    mid = normalize_message_id(message_ids[0])
    if not mid:
        return None
    account = db.load_settings(engine).gmail_address
    auth = f"?authuser={quote(account, safe='@')}" if account else ""
    return f"https://mail.google.com/mail/{auth}#search/{quote('rfc822msgid:' + mid, safe='')}"


def get_thread(engine: Engine, lead_id: int) -> dict | None:
    with engine.begin() as c:
        lead = c.execute(text("SELECT id, name, email, company, role, email_confidence, status, outcome FROM leads WHERE id = :i"),
                         {"i": lead_id}).mappings().first()
        if lead is None:
            return None
        entries: list[dict] = []
        mids: list[str] = []
        for m in c.execute(text(
                "SELECT id, kind, subject, body, sent_at, state, message_id FROM messages "
                "WHERE lead_id = :i AND sent_at IS NOT NULL ORDER BY sent_at, id"), {"i": lead_id}).mappings():
            entries.append({"kind": "sent", "at": m["sent_at"], "subject": m["subject"], "text": m["body"],
                            "message_id": m["id"], "message_state": m["state"], "send_kind": m["kind"]})
            if m["message_id"]:
                mids.append(m["message_id"])
        for e in c.execute(text(
                "SELECT id, type, snippet, subject, occurred_at, status_code, read_at, confirmed_at FROM events "
                "WHERE lead_id = :i ORDER BY occurred_at, id"), {"i": lead_id}).mappings():
            entries.append({"kind": e["type"], "at": e["occurred_at"], "subject": e["subject"], "text": e["snippet"],
                            "event_id": e["id"], "status_code": e["status_code"], "unread": e["read_at"] is None,
                            "needs_confirmation": e["type"] == "opt_out" and e["confirmed_at"] is None})
        c.execute(text("UPDATE events SET read_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                       "WHERE lead_id = :i AND read_at IS NULL"), {"i": lead_id})
    entries.sort(key=lambda x: (x["at"] or "", 0 if x["kind"] == "sent" else 1))
    return {"lead": _lead_out(lead), "entries": entries, "gmail_url": gmail_url(engine, mids)}


def confirm_opt_out(engine: Engine, event_id: int) -> bool:
    with engine.begin() as c:
        res = c.execute(text("UPDATE events SET confirmed_at = COALESCE(confirmed_at, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                             "WHERE id = :i AND type = 'opt_out'"), {"i": event_id})
        return res.rowcount > 0


def set_status(engine: Engine, lead_id: int, status: str | None, outcome: str | None, notes: str | None) -> dict | None:
    """Manual status / outcome / notes. Refusals raise StatusRefused (409 = conflicts with what is on record,
    422 = not a status a person can set)."""
    with engine.begin() as c:
        lead = c.execute(text("SELECT id, email, status FROM leads WHERE id = :i"), {"i": lead_id}).mappings().first()
        if lead is None:
            return None
        sets: dict = {}
        if status is not None and status != lead["status"]:
            suppressed = c.execute(text("SELECT 1 FROM suppression WHERE email = :e"), {"e": lead["email"]}).first() is not None
            if status in SYSTEM_ONLY:
                raise StatusRefused(422, f"'{status.replace('_', ' ')}' is set automatically; choose replied, "
                                         "opted out or closed.")
            if suppressed and status not in SUPPRESSED_OK:
                raise StatusRefused(409, "This address is on the suppression list, so it can only be marked opted out, "
                                         "bounced or closed. Suppressed addresses are never contacted again.")
            if lead["status"] in TERMINAL_FACTS and status not in TERMINAL_FACTS | {"closed"}:
                raise StatusRefused(409, f"This lead is {lead['status'].replace('_', ' ')}; it can not be reopened.")
            if status in {"replied", "sent"}:
                sent = c.execute(text("SELECT 1 FROM messages WHERE lead_id = :i AND sent_at IS NOT NULL"), {"i": lead_id}).first()
                if not sent:
                    raise StatusRefused(422, f"No email was sent to this lead, so it can not be marked {status}.")
            sets["status"] = status
            if status == "opted_out":  # a manual opt-out suppresses the address like an automatic one
                c.execute(text("INSERT OR IGNORE INTO suppression (email, reason) VALUES (:e, 'opt-out (set by you)')"),
                          {"e": lead["email"]})
        if outcome is not None:
            if outcome not in OUTCOMES:
                raise StatusRefused(422, "Unknown outcome.")
            sets["outcome"] = outcome
        if notes is not None:
            sets["notes"] = notes.strip() or None
        if sets:
            assign = ", ".join(f"{k} = :{k}" for k in sets)
            c.execute(text(f"UPDATE leads SET {assign}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = :id"),
                      {**sets, "id": lead_id})
        row = c.execute(text("SELECT id, name, email, company, role, email_confidence, status, outcome FROM leads WHERE id = :i"),
                        {"i": lead_id}).mappings().first()
    events.emit("lead.updated", {"id": lead_id}, engine)
    return _lead_out(row)
