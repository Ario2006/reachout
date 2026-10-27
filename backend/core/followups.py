"""Follow-ups (milestone 6): up to two short, same-thread emails after the first one, only to people who stayed silent.

Rules (the plan and the tick share `_evaluate`, so what the screen says is what the worker does):

* Follow-up N is considered only when the PREVIOUS email of the chain (initial, then follow-up 1) is `sent`: not failed,
  cancelled or waiting for confirmation. It is due `followup_days[N-1]` days after the FIRST email went out (cumulative:
  day 4 and day 9 by default; `messages.claimed_at`, the sender's clock; `sent_at` is the fallback).
* Nothing is created for a person who replied, bounced, opted out, sent an auto-reply, is closed or is suppressed.
  These are facts about the lead, re-read on every tick.
* When due, the tick writes a draft `followup_N` (subject `Re: <original>`, body = template.followup_N_body rendered for
  the lead; no LLM) and, unless the campaign requires approval, approves it. Approved drafts are queued through
  `outbox.enqueue`, so the approval snapshot, caps, window, jitter, suppression, per-company limits and the opt-out
  footer apply exactly as for the first email. Nothing here talks to a mailer.
* A queued follow-up still goes through `outbox.check` at send time, which re-reads the lead status and the suppression
  list: a reply that arrives after queuing and before sending stops the send (and the sender cancels it).
* Idempotent: unique indexes (drafts and messages by campaign, lead, kind) mean running the tick twice, or after a crash
  between the draft and the message, never produces a second email of one kind and never loses a due one.
* Threading: `in_reply_to` is the Message-ID of the previous email, `references_text` all earlier Message-IDs, oldest
  first. They are written in the same statement that makes the message sendable: it is enqueued with a far-future
  `not_before` first, so the sender can never pick it up without its headers (a crash in between is repaired by the
  next tick).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timezone

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy import Engine, text

from backend.core import campaigns, composer, db, domains, event_types, outbox, services
from backend.core.drafts import flag

log = logging.getLogger("reachout.followups")

TICK_EVERY_S = 180
KINDS = ("followup_1", "followup_2")
_CHAIN = ("initial", "followup_1", "followup_2")
_HOLD = 1e15  # not_before while a follow-up is being enqueued: the sender cannot claim it before it has its headers
_DAY = 86400.0
_ACTIVE = ("running", "done")  # a draft or paused campaign gets no new follow-ups until it runs again


class FollowupNotFound(LookupError):
    """That person was never emailed in this campaign."""


class FollowupConflict(ValueError):
    """The follow-up can no longer be skipped: it is already sending or sent."""


# ============================================================================ helpers
def _days(raw: str | None) -> list[int]:
    try:
        days = [int(d) for d in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []
    return [d for d in days if d >= 0][: len(KINDS)]


def _ts(m: dict) -> float | None:
    """When a message went out, in the sender's clock: its claim time, else the stored sent_at."""
    if m.get("claimed_at") is not None:
        return float(m["claimed_at"])
    if m.get("sent_at"):
        try:
            dt = datetime.fromisoformat(str(m["sent_at"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return None


def _reply_subject(original: str | None) -> str:
    base = re.sub(r"^(\s*re\s*:\s*)+", "", original or "", flags=re.I).strip()
    return f"Re: {base}" if base else "Re: (no subject)"


def _trace(c, trace_id: str | None, kind: str, message: str, once: bool = False) -> None:
    if not trace_id:
        return
    if once and c.execute(text("SELECT 1 FROM trace_events WHERE trace_id=:t AND kind=:k AND message=:m"), {"t": trace_id, "k": kind, "m": message}).first():
        return
    c.execute(text("INSERT INTO trace_events (trace_id, kind, message) VALUES (:t, :k, :m)"), {"t": trace_id, "k": kind, "m": message})


def _headers(chain: dict[str, dict], idx: int) -> tuple[str | None, str | None]:
    """(In-Reply-To, References) for follow-up number idx+1: the previous email, and every earlier one, oldest first."""
    earlier = [chain[k]["message_id"] for k in _CHAIN[: idx + 1] if chain.get(k) and chain[k].get("message_id")]
    return (earlier[-1] if earlier else None), (" ".join(earlier) or None)


# ============================================================================ evaluation
@dataclass
class Step:
    kind: str
    state: str  # waiting | due | awaiting_approval | queued | sent | skipped
    reason: str | None
    due_at: float
    action: str | None = None  # tick only: "create" | "enqueue" | "repair" (queued without its headers)


def _blocked(lead: dict, suppressed: bool) -> str | None:
    if suppressed:
        return "This address is on the suppression list."
    if lead["status"] in outbox.BLOCKING_LEAD_STATUS:
        return f"The lead is marked {lead['status'].replace('_', ' ')}."
    return None


def _body_problem(tpl: dict | None, kind: str, n: int) -> str | None:
    """Why the follow-up text cannot be written from this template, or None."""
    if tpl is None:
        return "The campaign has no template, so there is no follow-up text."
    return _render_problem(tpl.get(f"{kind}_body") or "", n)


@lru_cache(maxsize=256)
def _render_problem(body: str, n: int) -> str | None:
    if not body.strip():
        return f"The template has no text for follow-up {n}."
    try:
        out = composer.render({"subject": "-", "body": body}, {}, "")
    except composer.TemplateError as e:
        return f"The follow-up {n} text could not be written: {e}"
    return None if out.body.strip() else f"The template text for follow-up {n} is empty once filled in."


def _evaluate(lead: dict, suppressed: bool, msgs: dict[str, dict], drafts: dict[str, dict], days: list[int],
              tpl: dict | None, campaign_state: str, now: float) -> list[Step]:
    """One Step per planned follow-up for a person whose first email was sent. Pure: no writes."""
    steps: list[Step] = []
    first = msgs.get("initial")
    prev_ts = _ts(first) if first and first["state"] == "sent" else None
    if prev_ts is None:
        return steps
    block = _blocked(lead, suppressed)
    active = campaign_state in _ACTIVE
    stop: str | None = None  # why later steps cannot happen yet; None while the chain is intact
    final = False  # True: the chain has ended for good (skipped), False: later steps just wait
    first_ts = prev_ts
    for idx, kind in enumerate(KINDS[: len(days)]):
        m, d = msgs.get(kind), drafts.get(kind)
        due = first_ts + days[idx] * _DAY
        n = idx + 1
        if stop is not None:
            steps.append(Step(kind, "skipped" if final else "waiting", stop, due))
            continue
        step: Step
        if m is not None and m["state"] == "sent":
            steps.append(Step(kind, "sent", None, _ts(m) or due))
            continue
        if m is not None:
            st = m["state"]
            if st == "queued" and block:
                step, stop, final = Step(kind, "skipped", block + " It will not be sent.", due), block, True
            elif st in ("queued", "sending", "needs_confirmation"):
                reason = ("Waiting for you to confirm whether it was sent." if st == "needs_confirmation"
                          else None if active and campaign_state == "running" else f"The campaign is {campaign_state}.")
                held = st == "queued" and (m.get("not_before") is None or m["not_before"] >= _HOLD / 2)
                step = Step(kind, "queued", reason, due if held or m.get("not_before") is None else m["not_before"], "repair" if held else None)
                stop, final = f"Follow-up {n} has not been sent yet.", False
            elif st == "failed":
                step, stop, final = Step(kind, "skipped", f"Sending failed: {m.get('error') or 'unknown error'}", due), f"Follow-up {n} was not sent.", True
            else:
                step, stop, final = Step(kind, "skipped", m.get("error") or "Cancelled.", due), f"Follow-up {n} was not sent.", True
        elif block:
            step, stop, final = Step(kind, "skipped", block, due), block, True
        elif d is not None and d["status"] == "rejected":
            step, stop, final = Step(kind, "skipped", "You rejected this follow-up.", due), f"Follow-up {n} was rejected.", True
        elif now < due:
            step, stop, final = Step(kind, "waiting", None, due), f"Follow-up {n} has not been sent yet.", False
        elif d is not None and d["status"] == "approved":
            step = Step(kind, "due", None if active else f"The campaign is {campaign_state}.", due, "enqueue" if active else None)
            stop, final = f"Follow-up {n} has not been sent yet.", False
        elif d is not None:
            step = Step(kind, "awaiting_approval", "Approve this follow-up in the draft review to send it.", due)
            stop, final = f"Follow-up {n} has not been sent yet.", False
        elif (problem := _body_problem(tpl, kind, n)) is not None:
            step, stop, final = Step(kind, "skipped", problem, due), "There is no follow-up text.", True
        else:
            step = Step(kind, "due", None if active else f"The campaign is {campaign_state}.", due, "create" if active else None)
            stop, final = f"Follow-up {n} has not been sent yet.", False
        steps.append(step)
    return steps


def _load(c, campaign_id: int):
    cp = dict(c.execute(text("SELECT * FROM campaigns WHERE id=:i"), {"i": campaign_id}).mappings().one())
    leads: dict[int, dict] = {}
    msgs: dict[int, dict[str, dict]] = {}
    drafts: dict[int, dict[str, dict]] = {}
    for r in c.execute(text(
            "SELECT id, lead_id, kind, state, message_id, subject, claimed_at, sent_at, error, not_before FROM messages WHERE campaign_id=:c"),
            {"c": campaign_id}).mappings():
        msgs.setdefault(r["lead_id"], {})[r["kind"]] = dict(r)
    for r in c.execute(text("SELECT id, lead_id, kind, status FROM drafts WHERE campaign_id=:c AND kind != 'initial'"), {"c": campaign_id}).mappings():
        drafts.setdefault(r["lead_id"], {})[r["kind"]] = dict(r)
    for r in c.execute(text("SELECT * FROM leads WHERE id IN (SELECT lead_id FROM messages WHERE campaign_id=:c AND kind='initial' AND state='sent') ORDER BY id"),
                       {"c": campaign_id}).mappings():
        leads[r["id"]] = dict(r)
    sup = {r[0] for r in c.execute(text("SELECT email FROM suppression"))}
    return cp, leads, msgs, drafts, sup


def _template(engine: Engine, cp: dict) -> dict | None:
    return composer.get_template(cp["template_id"], engine) if cp.get("template_id") else None


# ============================================================================ the tick
def _create_draft(engine: Engine, cp: dict, lead: dict, kind: str, tpl: dict, original_subject: str | None, settings, profile: dict[str, str]) -> bool:
    """Write the follow-up draft (approved unless the campaign requires approval). False when it cannot be written."""
    try:
        r = composer.render({"subject": "-", "body": tpl[f"{kind}_body"]}, composer.lead_values(lead, settings, profile), "")
    except composer.TemplateError as e:
        with engine.begin() as c:
            _trace(c, lead.get("trace_id"), "followup", f"Follow-up not written: {e}", once=True)
        return False
    if not r.body.strip():
        return False
    flags = [flag("missing_field", "Empty merge fields: " + ", ".join(r.missing) + ".")] if r.missing else []
    approve = not cp.get("followups_require_approval")
    with engine.begin() as c:
        res = c.execute(text(
            "INSERT INTO drafts (campaign_id, lead_id, kind, subject, body, opener_source, flags_json, status, approved, approved_at) "
            "VALUES (:c, :l, :k, :s, :b, 'none', :f, :st, :ap, CASE WHEN :ap THEN datetime('now') END) "
            "ON CONFLICT (campaign_id, lead_id, kind) DO NOTHING"),
            {"c": cp["id"], "l": lead["id"], "k": kind, "s": _reply_subject(original_subject), "b": r.body, "f": json.dumps(flags),
             "st": "approved" if approve else "pending", "ap": int(approve)})
        if res.rowcount:
            _trace(c, lead.get("trace_id"), "followup", f"Wrote {kind.replace('_', ' ')} ({'approved automatically' if approve else 'waiting for your approval'})")
    if res.rowcount:
        event_types.publish("draft", event_types.DraftEvent(campaign_id=cp["id"], draft_id=None, action="generated"), engine)
    return True


def _release(engine: Engine, mid: int, chain: dict[str, dict], idx: int, due: float) -> None:
    """Give a held follow-up its threading headers and its real not_before in ONE statement."""
    irt, refs = _headers(chain, idx)
    with engine.begin() as c:
        c.execute(text("UPDATE messages SET in_reply_to=:i, references_text=:r, not_before=:nb WHERE id=:id AND state='queued'"),
                  {"i": irt, "r": refs, "nb": due, "id": mid})


def _enqueue(engine: Engine, cp: dict, lead: dict, kind: str, chain: dict[str, dict], idx: int, due: float) -> bool:
    try:
        mid, created = outbox.enqueue(lead["id"], cp["id"], kind, "", engine, not_before=_HOLD)
    except outbox.NotApproved:
        return False  # approval was withdrawn in the meantime; the next tick looks again
    if created:
        _release(engine, mid, chain, idx, due)
        event_types.publish("message", event_types.MessageEvent(campaign_id=cp["id"], lead_id=lead["id"], message_id=mid, kind=kind, state="queued"), engine)  # type: ignore[arg-type]
    return created


def _tick_campaign(engine: Engine, campaign_id: int, now: float) -> dict:
    out = {"drafts": 0, "queued": 0, "reopened": False}
    with engine.connect() as c:
        cp, leads, msgs, drafts, sup = _load(c, campaign_id)
    days = _days(cp["followup_days_json"])
    if not cp["followups_enabled"] or not days or cp["state"] not in _ACTIVE:
        return out
    tpl = _template(engine, cp)
    settings = db.load_settings(engine)
    profile = composer.profile_values(engine)
    for lead_id, lead in leads.items():
        try:
            chain = msgs.get(lead_id, {})
            for idx, s in enumerate(_evaluate(lead, lead["email"] in sup, chain, drafts.get(lead_id, {}), days, tpl, cp["state"], now)):
                if s.action == "repair":
                    _release(engine, chain[s.kind]["id"], chain, idx, s.due_at)
                elif s.action == "create" and tpl is not None:
                    if not _create_draft(engine, cp, lead, s.kind, tpl, chain["initial"].get("subject"), settings, profile):
                        continue
                    out["drafts"] += 1
                    if not cp.get("followups_require_approval") and _enqueue(engine, cp, lead, s.kind, chain, idx, s.due_at):
                        out["queued"] += 1
                elif s.action == "enqueue" and _enqueue(engine, cp, lead, s.kind, chain, idx, s.due_at):
                    out["queued"] += 1
        except Exception:  # noqa: BLE001 - one bad lead must not stop the others
            log.exception("follow-up tick failed for lead %s", lead_id)
    with engine.connect() as c:
        waiting = c.execute(text("SELECT COUNT(*) FROM messages WHERE campaign_id=:c AND state='queued' AND kind != 'initial'"), {"c": campaign_id}).scalar_one()
    if waiting and cp["state"] == "done":
        out["reopened"] = campaigns.reopen_if_done(campaign_id, engine)
        if out["reopened"]:
            campaigns.publish_campaign(campaign_id, engine)
    return out


def _cancel_disabled(engine: Engine) -> int:
    """Follow-ups turned off for a campaign: queued ones are cancelled and pending drafts rejected (once; idempotent)."""
    reason = "Follow-ups turned off."
    with engine.connect() as c:
        queued = c.execute(text("SELECT m.id, m.lead_id, m.campaign_id, m.kind FROM messages m JOIN campaigns cp ON cp.id = m.campaign_id "
                                "WHERE cp.followups_enabled = 0 AND m.kind != 'initial' AND m.state = 'queued'")).all()
    n = 0
    for mid, lead_id, cid, kind in queued:
        if outbox.cancel(mid, reason, engine):
            n += 1
            event_types.publish("message", event_types.MessageEvent(campaign_id=cid, lead_id=lead_id, message_id=mid, kind=kind, state="cancelled"), engine)  # type: ignore[arg-type]
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='rejected', approved=0, approved_at=NULL, updated_at=datetime('now') WHERE kind != 'initial' "
                       "AND status IN ('pending','needs_review') AND campaign_id IN (SELECT id FROM campaigns WHERE followups_enabled = 0)"))
    return n


def tick(engine: Engine, now: float | None = None) -> dict:
    """One pass over every campaign with follow-ups enabled. Safe to run as often as you like. Returns counts."""
    now = services.get_services().clock.now() if now is None else now
    total = {"drafts": 0, "queued": 0, "reopened": 0}
    _cancel_disabled(engine)
    with engine.connect() as c:
        ids = [r[0] for r in c.execute(text("SELECT id FROM campaigns WHERE followups_enabled=1 AND state IN ('running','done') ORDER BY id"))]
    for cid in ids:
        try:
            r = _tick_campaign(engine, cid, now)
        except Exception:  # noqa: BLE001
            log.exception("follow-up tick failed for campaign %s", cid)
            continue
        total["drafts"] += r["drafts"]
        total["queued"] += r["queued"]
        total["reopened"] += int(r["reopened"])
    return total


def register(sched: BaseScheduler, engine: Engine) -> None:
    """Adds the follow-up tick to the worker's scheduler."""
    sched.add_job(lambda: tick(engine), "interval", seconds=TICK_EVERY_S, id="followups_tick", max_instances=1, coalesce=True)


# ============================================================================ plan (read) and skip (write)
def plan(campaign_id: int, engine: Engine, now: float | None = None) -> dict | None:
    """Every planned follow-up for people whose first email was sent, with its state and the reason. None: no such campaign."""
    now = services.get_services().clock.now() if now is None else now
    with engine.connect() as c:
        if c.execute(text("SELECT 1 FROM campaigns WHERE id=:i"), {"i": campaign_id}).first() is None:
            return None
        cp, leads, msgs, drafts, sup = _load(c, campaign_id)
    days = _days(cp["followup_days_json"])
    if not days:
        days = [0] * len(KINDS)  # follow-ups with no schedule: show only what already exists
    tpl = _template(engine, cp)
    rows = []
    for lead_id, lead in leads.items():
        chain = msgs.get(lead_id, {})
        for s in _evaluate(lead, lead["email"] in sup, chain, drafts.get(lead_id, {}), days, tpl, cp["state"], now):
            if not cp["followups_enabled"] and s.kind not in chain:
                continue  # turned off: hide what has not happened, keep the history
            rows.append(dict(lead_id=lead_id, name=lead["name"], email=lead["email"], kind=s.kind, due_at=s.due_at, state=s.state, reason=s.reason))
    rows.sort(key=lambda r: (r["due_at"], r["lead_id"], r["kind"]))
    counts = {k: 0 for k in ("waiting", "due", "awaiting_approval", "queued", "sent", "skipped")}
    for r in rows:
        counts[r["state"]] += 1
    return dict(campaign_id=campaign_id, rows=rows, counts=counts)


def skip(campaign_id: int, lead_id: int, kind: str, reason: str | None, engine: Engine) -> None:
    """Withdraw one follow-up: a queued one is cancelled; one not yet queued (waiting, due, awaiting approval) is recorded
    as a cancelled message so it is never written again. Idempotent. Raises FollowupNotFound / FollowupConflict."""
    reason = (reason or "").strip()[:300] or "Skipped by you."
    with engine.connect() as c:
        lead = c.execute(text("SELECT l.email, l.trace_id FROM leads l WHERE l.id=:l AND EXISTS "
                              "(SELECT 1 FROM messages m WHERE m.campaign_id=:c AND m.lead_id=l.id AND m.kind='initial')"),
                         {"l": lead_id, "c": campaign_id}).first()
        m = c.execute(text("SELECT id, state FROM messages WHERE campaign_id=:c AND lead_id=:l AND kind=:k"), {"c": campaign_id, "l": lead_id, "k": kind}).first()
    if lead is None:
        raise FollowupNotFound("That person was not emailed in this campaign.")
    mid = 0
    if m is not None:
        mid, state = m
        if state == "cancelled":
            return
        if state in ("sending", "sent", "needs_confirmation"):
            raise FollowupConflict(f"This follow-up is already {state.replace('_', ' ')}, so it can no longer be skipped.")
        if state == "queued":
            outbox.cancel(mid, reason, engine)
        else:  # failed: nothing reached the server, and the user gives up on it
            with engine.begin() as c:
                c.execute(text("UPDATE messages SET state='cancelled', error=:e WHERE id=:i AND state='failed'"), {"e": reason, "i": mid})
    else:
        email = lead[0].lower()
        with engine.begin() as c:
            res = c.execute(text(
                "INSERT OR IGNORE INTO messages (lead_id, campaign_id, kind, state, error, recipient_email, recipient_domain, trace_id) "
                "VALUES (:l, :c, :k, 'cancelled', :e, :em, :d, :t)"),
                {"l": lead_id, "c": campaign_id, "k": kind, "e": reason, "em": email, "d": domains.registrable(domains.split_email(email)[1]), "t": lead[1]})
            mid = int(res.lastrowid) if res.rowcount else 0
            _trace(c, lead[1], "cancelled", f"{kind.replace('_', ' ').capitalize()} skipped: {reason}"[:200])
    with engine.begin() as c:  # a follow-up waiting for approval leaves the review queue
        c.execute(text("UPDATE drafts SET status='rejected', approved=0, approved_at=NULL, updated_at=datetime('now') "
                       "WHERE campaign_id=:c AND lead_id=:l AND kind=:k AND status IN ('pending','needs_review')"), {"c": campaign_id, "l": lead_id, "k": kind})
    event_types.publish("message", event_types.MessageEvent(campaign_id=campaign_id, lead_id=lead_id, message_id=mid, kind=kind, state="cancelled"), engine)  # type: ignore[arg-type]
