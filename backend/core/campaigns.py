"""Campaign records: create, read, update, delete. Starting, pausing and sending belong to the sender (milestone 4)."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Engine, text

from backend.core import db
from backend.core.models import risk_warnings

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
