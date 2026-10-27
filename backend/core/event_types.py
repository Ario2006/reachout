"""SSE event contract for milestones 3-6. Existing events (job, run, run_domain, run_log, lead, lead.updated) are
unchanged. Emit through `publish()` so the payload shape is validated; the frontend mirrors these in
desktop/src/lib/event-types.ts (zod). Payloads are small invalidation hints: the UI refetches the data."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Engine

from backend.core import events


class DraftEvent(BaseModel):
    campaign_id: int
    draft_id: int | None = None  # None = bulk change
    action: Literal["generated", "updated", "approved", "rejected", "bulk"]


class CampaignEvent(BaseModel):
    campaign_id: int
    state: Literal["draft", "running", "paused", "done"]
    paused_by: str | None = None
    pause_reason: str | None = None
    next_send_at: float | None = None


class MessageEvent(BaseModel):
    campaign_id: int
    lead_id: int
    message_id: int  # messages.id
    kind: Literal["initial", "followup_1", "followup_2"]
    state: Literal["queued", "sending", "sent", "failed", "needs_confirmation", "cancelled"]


class InboxEvent(BaseModel):
    lead_id: int
    event_id: int
    type: Literal["reply", "bounce", "auto_reply", "opt_out"]


class SendingEvent(BaseModel):
    """Sidebar footer: pause-all switch and today's sends against the cap."""
    paused_all: bool
    sent_today: int
    cap: int


MODELS: dict[str, type[BaseModel]] = {
    "draft": DraftEvent,
    "campaign": CampaignEvent,
    "message": MessageEvent,
    "inbox": InboxEvent,
    "sending": SendingEvent,
}


def publish(type_: str, payload: BaseModel, engine: Engine | None = None) -> int:
    if MODELS.get(type_) is not type(payload):
        raise TypeError(f"{type_!r} expects {MODELS.get(type_)}, got {type(payload)}")
    return events.emit(type_, payload.model_dump(mode="json"), engine)
