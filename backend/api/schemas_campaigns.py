"""API contract for Campaigns and sending (milestone 4). Basic CRUD is implemented in Phase 0 (needed by Compose);
everything else is owner C's."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.api.schemas import JobOut
from backend.core.models import Mode

CampaignState = Literal["draft", "running", "paused", "done"]
PausedBy = Literal["user", "bounce_rate", "smtp_login", "shutdown", "all"]
MessageState = Literal["queued", "sending", "sent", "failed", "needs_confirmation", "cancelled"]
Kind = Literal["initial", "followup_1", "followup_2"]


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template_id: int | None = None
    mode: Mode | None = None
    attachment_path: str | None = None  # one PDF; a warning is returned above 2 MB
    daily_cap: int = Field(25, ge=1, le=100)
    window_start: str = Field("09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    window_end: str = Field("18:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    delay_min_s: int | None = Field(None, ge=0)  # None = Settings
    delay_max_s: int | None = Field(None, ge=0)
    followups_enabled: bool = False
    followup_days: list[int] = Field(default_factory=lambda: [4, 9], max_length=2)
    followups_require_approval: bool = False


class CampaignCounts(BaseModel):
    drafts_pending: int = 0
    drafts_needs_review: int = 0
    drafts_approved: int = 0
    queued: int = 0
    sending: int = 0
    sent: int = 0
    failed: int = 0
    needs_confirmation: int = 0


class CampaignOut(CampaignIn):
    id: int
    state: CampaignState
    pause_reason: str | None
    paused_by: PausedBy | None
    next_send_at: float | None
    test_sent_at: str | None
    dry_run_at: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    counts: CampaignCounts
    warnings: list[str] = []  # e.g. attachment over 2 MB, cap above the recommended value


class ReadinessItem(BaseModel):
    id: Literal["gmail", "approved_drafts", "attachment", "test_sent", "needs_confirmation"]
    label: str
    ok: bool
    detail: str


class Readiness(BaseModel):
    items: list[ReadinessItem]
    ready: bool


class CampaignStats(BaseModel):
    approved: int
    queued: int
    sent: int
    failed: int
    needs_confirmation: int
    bounced: int
    replied: int
    sent_today: int
    daily_cap_effective: int
    bounce_rate: float | None  # percent, None until enough sends
    next_send_at: float | None


class SendingStatus(BaseModel):
    paused_all: bool
    sent_today: int
    cap: int
    in_flight: int
    next_send_at: float | None
    worker_alive: bool
    caffeinate_active: bool


class TestSendOut(BaseModel):
    to: str
    message_id: str


class DryRunLine(BaseModel):
    lead_id: int
    email: str
    subject: str
    decision: Literal["would_send", "blocked"]
    reason: str | None


DryRunOut = JobOut  # result: {"lines": [DryRunLine...], "would_send": n, "blocked": m}


class MessageOut(BaseModel):
    id: int
    campaign_id: int
    lead_id: int
    lead_name: str | None
    recipient_email: str
    kind: Kind
    state: MessageState
    subject: str | None
    sent_at: str | None
    error: str | None


class ConfirmMessageIn(BaseModel):
    was_sent: bool
