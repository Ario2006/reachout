"""Drafts (milestone 3): generation job, review queue queries, edit / approve / reject, bulk approve, regenerate opener.

Rules enforced here (and by the schema):
* A draft stores the rendered body WITHOUT the footer; `final_text` adds it with `footer.ensure_footer`.
* Editing the subject or body of an approved draft withdraws the approval (trigger in migration 005).
* A `needs_review` draft is approved only by an explicit single approve, never in bulk.
* Suppressed addresses and leads in a blocking status cannot have a draft approved or generated."""
from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy import Engine, text

from backend.core import campaigns, composer, db, event_types, footer, personaliser
from backend.core.jobs import JobContext
from backend.core.outbox import BLOCKING_LEAD_STATUS

NO_DRAFT_STATUS = BLOCKING_LEAD_STATUS + ("queued", "sent")  # leads that must not get a new draft
OPENER_CODES = ("opener_fallback", "banned_phrase", "too_long", "unsupported_fact", "no_context")
STATUSES = ("pending", "needs_review", "approved", "rejected")


class DraftNotFound(LookupError):
    pass


class DraftConflict(ValueError):
    """The requested change is not allowed (blocked lead, review rule, no template)."""


def flag(code: str, message: str) -> dict:
    return {"code": code, "message": message}


# ----------------------------------------------------------------------------- generation
def _opener_flags(op: personaliser.Opener) -> list[dict]:
    if op.no_context:
        return [flag("no_context", "No source text was saved for this lead, so a neutral opener was used.")]
    if op.source == "llm":
        return []
    out = [flag("opener_fallback", op.error or "The model's opener failed the checks twice; a neutral opener was used.")]
    seen: set[str] = set()
    for code, message in op.problems:
        if code not in seen:
            seen.add(code)
            out.append(flag(code, message))
    return out


def build(template: dict, lead: dict, settings, profile: dict[str, str], op: personaliser.Opener) -> dict:
    """Everything a draft row needs for one lead (no database writes)."""
    r = composer.render(template, composer.lead_values(lead, settings, profile), op.text)
    flags = _opener_flags(op)
    if lead.get("email_confidence") == "guessed":
        flags.append(flag("guessed_email", "The address was guessed from a pattern, not published."))
    if r.missing:
        flags.append(flag("missing_field", "Empty merge fields: " + ", ".join(r.missing) + "."))
    review = op.source == "fallback" and not op.no_context
    return dict(subject=r.subject, body=r.body, opener=op.text, opener_source=op.source, flags=flags,
                status="needs_review" if review else "pending")


def _sync_lead_status(c, lead_id: int) -> None:
    """lead.status follows its initial drafts, but only while it is still at the start of the pipeline."""
    cur = c.execute(text("SELECT status FROM leads WHERE id = :i"), {"i": lead_id}).scalar()
    if cur not in ("new", "drafted", "approved"):
        return
    states = {r[0] for r in c.execute(text("SELECT status FROM drafts WHERE lead_id = :i AND kind = 'initial'"), {"i": lead_id})}
    new = "approved" if "approved" in states else "drafted" if states & {"pending", "needs_review"} else "new"
    if new != cur:
        c.execute(text("UPDATE leads SET status = :s WHERE id = :i"), {"s": new, "i": lead_id})


def _save(engine: Engine, campaign_id: int, lead_id: int, d: dict) -> int:
    """Insert, or replace an unapproved initial draft (an approved one is kept). Returns the draft id."""
    row = dict(c=campaign_id, l=lead_id, subject=d["subject"], body=d["body"], opener=d["opener"], src=d["opener_source"],
               flags=json.dumps(d["flags"]), status=d["status"])
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO drafts (campaign_id, lead_id, kind, subject, body, opener, opener_source, flags_json, status, approved) "
            "VALUES (:c, :l, 'initial', :subject, :body, :opener, :src, :flags, :status, 0) "
            "ON CONFLICT (campaign_id, lead_id, kind) DO UPDATE SET subject = excluded.subject, body = excluded.body, "
            "opener = excluded.opener, opener_source = excluded.opener_source, flags_json = excluded.flags_json, "
            "status = excluded.status, approved = 0, approved_at = NULL, updated_at = datetime('now') "
            "WHERE drafts.status <> 'approved'"), row)
        draft_id = c.execute(text("SELECT id FROM drafts WHERE campaign_id = :c AND lead_id = :l AND kind = 'initial'"), row).scalar_one()
        _sync_lead_status(c, lead_id)
    return int(draft_id)


def _candidates(engine: Engine, campaign: dict, template: dict, lead_ids: list[int] | None, regenerate: bool) -> tuple[list[dict], int]:
    """(leads to write drafts for, number skipped before any model call)."""
    with engine.connect() as c:
        if lead_ids is not None:
            ids = list(dict.fromkeys(lead_ids))
            rows = [r for i in ids if (r := c.execute(text("SELECT * FROM leads WHERE id = :i"), {"i": i}).first())]
            skipped = len(ids) - len(rows)
        else:
            mode = campaign.get("mode") or template.get("mode")
            rows = c.execute(text("SELECT * FROM leads WHERE (:m IS NULL OR mode = :m) ORDER BY id"), {"m": mode}).all()
            skipped = 0
        have = {r[0]: r[1] for r in c.execute(
            text("SELECT lead_id, status FROM drafts WHERE campaign_id = :c AND kind = 'initial'"), {"c": campaign["id"]})}
    out = []
    for r in rows:
        lead = dict(r._mapping)
        st = have.get(lead["id"])
        if st is not None and (not regenerate or st == "approved"):
            skipped += lead_ids is not None  # an explicit pick that is passed over is counted; the implicit sweep is not
            continue
        if lead["status"] in NO_DRAFT_STATUS or db.is_suppressed(lead["email"], engine):
            skipped += 1
            continue
        out.append(lead)
    return out, skipped


def run_generate_drafts(ctx: JobContext, payload: dict) -> dict:
    """Job `drafts_generate`. payload = GenerateDraftsIn. Result: {"generated": n, "needs_review": n, "skipped": n}.
    Leads are processed one at a time (one model call at a time); cancelling keeps the drafts already written."""
    engine = ctx.engine
    cid = int(payload["campaign_id"])
    campaign = campaigns.get(cid, engine)
    if campaign is None:
        raise ValueError("The campaign no longer exists.")
    template = composer.get_template(campaign["template_id"], engine) if campaign.get("template_id") else None
    if template is None:
        raise ValueError("Pick a template for this campaign before generating drafts.")
    settings, profile = db.load_settings(engine), composer.profile_values(engine)
    leads, skipped = _candidates(engine, campaign, template, payload.get("lead_ids"), bool(payload.get("regenerate")))
    total = len(leads)
    generated = review = 0
    errors: list[str] = []
    ctx.progress(0, total, f"Writing {total} draft{'s' if total != 1 else ''}", force=True)
    for i, lead in enumerate(leads):
        ctx.raise_if_cancelled()
        ctx.progress(i, total, f"Writing draft {i + 1} of {total}")
        try:
            d = build(template, lead, settings, profile, personaliser.make_opener(lead, engine=engine))
        except composer.TemplateError as e:  # one lead's template trouble must not sink the whole job
            errors.append(str(e))
            skipped += 1
            continue
        draft_id = _save(engine, cid, lead["id"], d)
        generated += 1
        review += d["status"] == "needs_review"
        _emit(engine, cid, draft_id, "generated")
    if errors and generated == 0:
        raise ValueError(errors[0])
    msg = f"{generated} draft{'s' if generated != 1 else ''} written"
    if review:
        msg += f", {review} need review"
    if skipped:
        msg += f", {skipped} skipped"
    ctx.progress(total, total, msg, force=True)
    _emit(engine, cid, None, "bulk")
    return {"generated": generated, "needs_review": review, "skipped": skipped}


# ----------------------------------------------------------------------------- reading
_SELECT = (
    "SELECT d.*, l.name AS l_name, l.email AS l_email, l.role AS l_role, l.company AS l_company, "
    "l.email_confidence AS l_conf, l.context_snippet AS l_snip, l.source_url AS l_src, l.mode AS l_mode, "
    "c.mode AS c_mode, t.mode AS t_mode "
    "FROM drafts d JOIN leads l ON l.id = d.lead_id JOIN campaigns c ON c.id = d.campaign_id "
    "LEFT JOIN templates t ON t.id = c.template_id")


def _out(r, settings, detail: bool = False) -> dict:
    m = r._mapping
    body, opener = m["body"], m["opener"]
    at = body.find(opener) if opener else -1
    out = dict(
        id=m["id"], campaign_id=m["campaign_id"], kind=m["kind"],
        lead=dict(id=m["lead_id"], name=m["l_name"], email=m["l_email"], role=m["l_role"], company=m["l_company"],
                  email_confidence=m["l_conf"], context_snippet=m["l_snip"], source_url=m["l_src"]),
        subject=m["subject"], body=body, opener=opener, opener_source=m["opener_source"],
        opener_span=dict(start=at, end=at + len(opener)) if at >= 0 and opener else None,
        flags=json.loads(m["flags_json"] or "[]"), status=m["status"], updated_at=m["updated_at"])
    if detail:
        out["final_text"] = footer.ensure_footer(body, settings, m["c_mode"] or m["t_mode"] or m["l_mode"])
    return out


def get_draft(draft_id: int, engine: Engine) -> dict | None:
    with engine.connect() as c:
        r = c.execute(text(_SELECT + " WHERE d.id = :i"), {"i": draft_id}).first()
    return _out(r, db.load_settings(engine), detail=True) if r else None


def list_drafts(engine: Engine, campaign_id: int, statuses: list[str] | None = None, flagged: bool | None = None,
                q: str = "", offset: int = 0, limit: int = 100, kind: str | None = None) -> dict:
    where, p = ["d.campaign_id = :cid"], {"cid": campaign_id}
    if kind:
        where.append("d.kind = :kind")
        p["kind"] = kind
    base = " AND ".join(where)
    if statuses:
        where.append("d.status IN (" + ", ".join(f":s{i}" for i in range(len(statuses))) + ")")
        p.update({f"s{i}": s for i, s in enumerate(statuses)})
    if flagged is not None:
        where.append("json_array_length(d.flags_json) " + (">" if flagged else "=") + " 0")
    if q.strip():
        p["q"] = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(" + " OR ".join(f"lower(COALESCE({col}, '')) LIKE :q ESCAPE '\\'"
                                       for col in ("l.name", "l.email", "l.company", "d.subject")) + ")")
    w = " WHERE " + " AND ".join(where)
    with engine.connect() as c:
        total = c.execute(text("SELECT COUNT(*) FROM drafts d JOIN leads l ON l.id = d.lead_id" + w), p).scalar_one()
        rows = c.execute(text(_SELECT + w + " ORDER BY d.id LIMIT :lim OFFSET :off"),
                         {**p, "lim": max(1, min(limit, 500)), "off": max(0, offset)}).all()
        base_p = {k: v for k, v in p.items() if k in ("cid", "kind")}
        counts = dict(c.execute(text(f"SELECT status, COUNT(*) FROM drafts d WHERE {base} GROUP BY status"), base_p).all())
    settings = db.load_settings(engine)
    return dict(items=[_out(r, settings) for r in rows], total=total, counts={s: counts.get(s, 0) for s in STATUSES})


# ----------------------------------------------------------------------------- changing
def _blocked_reason(c, lead_id: int) -> str | None:
    """Why this lead may not be approved for sending, or None."""
    row = c.execute(text("SELECT email, status FROM leads WHERE id = :i"), {"i": lead_id}).first()
    if row is None:
        return "The lead no longer exists."
    if c.execute(text("SELECT 1 FROM suppression WHERE email = :e"), {"e": row[0].strip().lower()}).first():
        return "This address is on the suppression list."
    if row[1] in BLOCKING_LEAD_STATUS:
        return f"The lead is marked {row[1].replace('_', ' ')}."
    return None


def _emit(engine: Engine, campaign_id: int, draft_id: int | None, action: str) -> None:
    event_types.publish("draft", event_types.DraftEvent(campaign_id=campaign_id, draft_id=draft_id, action=action), engine)  # type: ignore[arg-type]


def patch_draft(draft_id: int, patch: dict, engine: Engine) -> dict:
    """Edit subject/body and/or set status (pending, approved, rejected). `patch` holds only the fields sent."""
    action = "updated"
    with engine.begin() as c:
        r = c.execute(text("SELECT * FROM drafts WHERE id = :i"), {"i": draft_id}).first()
        if r is None:
            raise DraftNotFound(draft_id)
        d = dict(r._mapping)
        subject, body = patch.get("subject") or d["subject"], patch.get("body") or d["body"]
        if (subject, body) != (d["subject"], d["body"]):
            flags = json.loads(d["flags_json"])
            if not any(f["code"] == "edited" for f in flags):
                flags.append(flag("edited", "Edited by hand after it was written."))
            opener, src = d["opener"], d["opener_source"]
            if opener and opener not in body:
                opener, src = None, "manual"
            c.execute(text("UPDATE drafts SET subject = :s, body = :b, opener = :o, opener_source = :src, flags_json = :f, "
                           "updated_at = datetime('now') WHERE id = :i"),
                      dict(s=subject, b=body, o=opener, src=src, f=json.dumps(flags), i=draft_id))  # trigger withdraws approval
        want = patch.get("status")
        cur = c.execute(text("SELECT status FROM drafts WHERE id = :i"), {"i": draft_id}).scalar_one()
        if want == "approved":
            why = _blocked_reason(c, d["lead_id"])
            if why:
                raise DraftConflict(why)
            c.execute(text("UPDATE drafts SET status = 'approved', approved = 1, approved_at = datetime('now'), "
                           "updated_at = datetime('now') WHERE id = :i"), {"i": draft_id})
            action = "approved"
        elif want == "rejected":
            c.execute(text("UPDATE drafts SET status = 'rejected', approved = 0, approved_at = NULL, "
                           "updated_at = datetime('now') WHERE id = :i"), {"i": draft_id})
            action = "rejected"
        elif want == "pending" and cur != "pending":
            if cur == "needs_review":
                raise DraftConflict("A draft that needs review stays in review until you approve or reject it.")
            c.execute(text("UPDATE drafts SET status = 'pending', approved = 0, approved_at = NULL, "
                           "updated_at = datetime('now') WHERE id = :i"), {"i": draft_id})
        # A fallback opener must stay behind the review gate however the draft got back to pending
        # (explicit un-approve, reject then pending, or an edit that withdrew approval).
        c.execute(text(
            "UPDATE drafts SET status = 'needs_review' WHERE id = :i AND status = 'pending' AND opener_source = 'fallback' "
            "AND NOT EXISTS (SELECT 1 FROM json_each(drafts.flags_json) WHERE json_extract(value, '$.code') = 'no_context')"),
            {"i": draft_id})
        _sync_lead_status(c, d["lead_id"])
    _emit(engine, d["campaign_id"], draft_id, action)
    return get_draft(draft_id, engine)  # type: ignore[return-value]


def bulk_approve(campaign_id: int, draft_ids: Iterable[int] | None, only_unflagged: bool, engine: Engine) -> dict:
    """Approve pending drafts. needs_review drafts are never touched here, whatever the arguments."""
    ids = None if draft_ids is None else set(draft_ids)
    with engine.begin() as c:
        rows = c.execute(text(
            "SELECT d.id, d.lead_id, json_array_length(d.flags_json) FROM drafts d "
            "WHERE d.campaign_id = :c AND d.status = 'pending' ORDER BY d.id"), {"c": campaign_id}).all()
        target, flagged, blocked = [], 0, 0
        for did, lid, nflags in rows:
            if ids is not None and did not in ids:
                continue
            if _blocked_reason(c, lid):
                blocked += 1
            elif only_unflagged and nflags:
                flagged += 1
            else:
                target.append((did, lid))
        for did, _ in target:
            c.execute(text("UPDATE drafts SET status = 'approved', approved = 1, approved_at = datetime('now'), "
                           "updated_at = datetime('now') WHERE id = :i AND status = 'pending'"), {"i": did})
        for lid in {lid for _, lid in target}:
            _sync_lead_status(c, lid)
    if target:
        _emit(engine, campaign_id, None, "bulk")
    return dict(approved=len(target), skipped_flagged=flagged, skipped_blocked=blocked)


def regenerate_opener(draft_id: int, engine: Engine) -> dict:
    """New opener for one draft. The old opener text is replaced in place, keeping hand edits elsewhere; if the opener
    was edited away, the body is re-rendered from the campaign's template."""
    with engine.connect() as c:
        r = c.execute(text("SELECT d.*, c.template_id AS tpl FROM drafts d JOIN campaigns c ON c.id = d.campaign_id "
                           "WHERE d.id = :i"), {"i": draft_id}).first()
        lead = c.execute(text("SELECT * FROM leads WHERE id = :i"), {"i": r.lead_id}).first() if r else None
    if r is None or lead is None:
        raise DraftNotFound(draft_id)
    d, lead = dict(r._mapping), dict(lead._mapping)
    op = personaliser.make_opener(lead, engine=engine, previous=d["opener"])
    flags = [f for f in json.loads(d["flags_json"]) if f["code"] not in OPENER_CODES] + _opener_flags(op)
    subject, body = d["subject"], d["body"]
    if d["opener"] and d["opener"] in body:
        body = body.replace(d["opener"], op.text, 1)
    else:
        tpl = composer.get_template(d["tpl"], engine) if d["tpl"] else None
        if tpl is None:
            raise DraftConflict("The opener was edited by hand and this campaign has no template to rebuild it from.")
        values = composer.lead_values(lead, db.load_settings(engine), composer.profile_values(engine))
        try:
            rendered = composer.render(tpl, values, op.text)
        except composer.TemplateError as e:
            raise DraftConflict(str(e)) from e
        subject, body = rendered.subject, rendered.body
        flags = [f for f in flags if f["code"] != "edited"]
    want = "needs_review" if op.source == "fallback" and not op.no_context else "pending"
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET subject = :s, body = :b, opener = :o, opener_source = :src, flags_json = :f, "
                       "updated_at = datetime('now') WHERE id = :i"),
                  dict(s=subject, b=body, o=op.text, src=op.source, f=json.dumps(flags), i=draft_id))  # approved -> pending by trigger
        cur = c.execute(text("SELECT status FROM drafts WHERE id = :i"), {"i": draft_id}).scalar_one()
        if cur != want:
            c.execute(text("UPDATE drafts SET status = :w, approved = 0, approved_at = NULL WHERE id = :i"), {"w": want, "i": draft_id})
        _sync_lead_status(c, d["lead_id"])
    _emit(engine, d["campaign_id"], draft_id, "updated")
    return get_draft(draft_id, engine)  # type: ignore[return-value]
