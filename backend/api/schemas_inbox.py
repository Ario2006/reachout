"""API contract for Inbox, Dashboard (milestone 5) and Follow-ups (milestone 6)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.api.schemas import JobOut
from backend.api.schemas_campaigns import Kind, MessageState
from backend.core.models import EmailConfidence, LeadStatus

EventType = Literal["reply", "bounce", "auto_reply", "opt_out"]
Outcome = Literal["interested", "not_interested", "meeting_booked"]
InboxFilter = Literal["all", "unread", "replied", "bounced", "opt_out", "auto_reply", "sent"]


class ThreadLead(BaseModel):
    id: int
    name: str | None
    email: str
    company: str | None
    role: str | None
    email_confidence: EmailConfidence
    status: LeadStatus
    outcome: Outcome | None


class ThreadSummary(BaseModel):
    lead: ThreadLead
    last_activity_at: str | None
    last_kind: Literal["sent", "reply", "bounce", "auto_reply", "opt_out"] | None
    snippet: str | None
    unread: int
    opt_out_to_confirm: bool


class ThreadsPage(BaseModel):
    items: list[ThreadSummary]
    total: int
    unread_total: int


class ThreadEntry(BaseModel):
    kind: Literal["sent", "reply", "bounce", "auto_reply", "opt_out"]
    at: str
    subject: str | None
    text: str | None  # our body for sent; snippet for incoming
    event_id: int | None = None
    message_id: int | None = None  # messages.id for sent entries
    message_state: MessageState | None = None
    send_kind: Kind | None = None
    status_code: str | None = None  # bounces
    unread: bool = False
    needs_confirmation: bool = False  # opt-out awaiting the user's one-click confirm


class ThreadDetail(BaseModel):
    lead: ThreadLead
    entries: list[ThreadEntry]
    gmail_url: str | None  # search link by Message-ID, never contains the password


class LeadStatusIn(BaseModel):
    status: LeadStatus | None = None
    outcome: Outcome | None = None
    notes: str | None = None


class InboxStatus(BaseModel):
    last_checked_at: float | None
    next_check_at: float | None
    last_error: str | None
    checking: bool


CheckNowOut = JobOut


# ---- dashboard
class Kpis(BaseModel):
    leads: int
    drafts_awaiting_review: int
    sent_today: int
    daily_cap: int
    sent_total: int
    replies: int
    reply_rate: float | None  # percent of delivered
    bounce_rate: float | None
    bounced: int
    opted_out: int
    followups_sent: int
    awaiting_followup: int
    delivered: int


class FunnelStage(BaseModel):
    stage: Literal["leads", "drafted", "approved", "sent", "delivered", "replied"]
    count: int


class DayCount(BaseModel):
    day: str  # YYYY-MM-DD, local
    sent: int
    replies: int = 0
    bounces: int = 0


class AttentionItem(BaseModel):
    kind: Literal["bounce_rate", "needs_review", "unread_replies", "followups_due", "needs_confirmation",
                  "opt_out_to_confirm", "campaign_paused", "gmail_login"]
    severity: Literal["info", "warning", "critical"]
    message: str
    count: int
    link: str  # in-app route, e.g. /compose or /inbox?filter=unread


class ActivityItem(BaseModel):
    at: str
    kind: str
    text: str
    lead_id: int | None


class Metrics(BaseModel):
    kpis: Kpis
    funnel: list[FunnelStage]
    per_day: list[DayCount]
    attention: list[AttentionItem]
    opens_note: str = "Opens are not tracked: it is unreliable and would need a public server."


# ---- follow-ups (milestone 6)
FollowupState = Literal["waiting", "due", "awaiting_approval", "queued", "sent", "skipped"]


class FollowupRow(BaseModel):
    lead_id: int
    name: str | None
    email: str
    kind: Literal["followup_1", "followup_2"]
    due_at: float
    state: FollowupState
    reason: str | None  # why skipped / why not sent


class FollowupPlan(BaseModel):
    campaign_id: int
    rows: list[FollowupRow]
    counts: dict[str, int]


class FollowupSkipIn(BaseModel):
    lead_id: int
    kind: Literal["followup_1", "followup_2"]
    reason: str | None = None  # shown in the plan; "Skipped by you." when empty
