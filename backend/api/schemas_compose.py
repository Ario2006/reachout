"""API contract for Compose (milestone 3): templates, profile fields, drafts. Owner: task B."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.api.schemas import JobOut
from backend.core.models import EmailConfidence, Mode

DraftStatus = Literal["pending", "needs_review", "approved", "rejected"]
DraftKind = Literal["initial", "followup_1", "followup_2"]
OpenerSource = Literal["llm", "fallback", "manual", "none"]
FlagCode = Literal["needs_review", "opener_fallback", "banned_phrase", "too_long", "unsupported_fact",
                   "missing_field", "guessed_email", "no_context", "edited"]


class TemplateIn(BaseModel):
    mode: Mode | None = None
    name: str = Field(min_length=1, max_length=120)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)  # Jinja2 with {{first_name}} {{company}} {{role}} {{my_name}} {{custom_opener}} + profile keys
    followup_1_body: str | None = None
    followup_2_body: str | None = None


class TemplateOut(TemplateIn):
    id: int
    is_starter: bool
    merge_fields: list[str]  # fields the body references, for the editor chips
    updated_at: str


class TemplatePreviewIn(BaseModel):
    template: TemplateIn
    lead_id: int | None = None  # None = a built-in sample lead


class TextSpan(BaseModel):
    start: int
    end: int


class TemplatePreview(BaseModel):
    subject: str
    body: str
    final_text: str  # body with the opt-out footer, exactly as it would be sent
    opener_span: TextSpan | None = None  # which characters are LLM-written
    missing_fields: list[str]
    word_count: int


class ProfileField(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=2000)


class ProfileOut(BaseModel):
    fields: list[ProfileField]
    reserved: list[str]  # first_name, company, role, my_name, custom_opener: not editable here


class ProfilePut(BaseModel):
    fields: list[ProfileField]


class DraftFlag(BaseModel):
    code: FlagCode
    message: str


class DraftLead(BaseModel):
    id: int
    name: str | None
    email: str
    role: str | None
    company: str | None
    email_confidence: EmailConfidence
    context_snippet: str | None
    source_url: str | None


class DraftOut(BaseModel):
    id: int
    campaign_id: int
    kind: DraftKind
    lead: DraftLead
    subject: str
    body: str
    opener: str | None
    opener_source: OpenerSource
    opener_span: TextSpan | None  # position of the opener inside `body`, so the UI can highlight it
    flags: list[DraftFlag]
    status: DraftStatus
    updated_at: str


class DraftCounts(BaseModel):
    pending: int = 0
    needs_review: int = 0
    approved: int = 0
    rejected: int = 0


class DraftsPage(BaseModel):
    items: list[DraftOut]
    total: int
    counts: DraftCounts


class DraftDetail(DraftOut):
    final_text: str  # body with the footer applied: what will actually be sent


class DraftPatch(BaseModel):
    subject: str | None = Field(None, min_length=1, max_length=200)
    body: str | None = Field(None, min_length=1, max_length=6000)
    status: Literal["pending", "approved", "rejected"] | None = None  # needs_review is only ever set by the generator

    @field_validator("subject")
    @classmethod
    def _subject(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("The subject cannot be blank.")
        if re.search(r"[\x00-\x1f\x7f]", v):
            raise ValueError("The subject must be a single line without control characters.")
        return v

    @field_validator("body")
    @classmethod
    def _body(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("The body cannot be blank.")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", v):
            raise ValueError("The body contains control characters.")
        return v


class GenerateDraftsIn(BaseModel):
    campaign_id: int
    lead_ids: list[int] | None = None  # None = every lead of the campaign's mode that has no draft yet
    regenerate: bool = False  # replace existing unapproved drafts


class BulkApproveIn(BaseModel):
    campaign_id: int
    draft_ids: list[int] | None = None  # None = all pending drafts of the campaign
    only_unflagged: bool = True
    kinds: list[DraftKind] = Field(default_factory=lambda: ['initial'])  # follow-up drafts are only approved when asked for


class BulkApproveOut(BaseModel):
    approved: int
    skipped_flagged: int
    skipped_blocked: int = 0  # suppressed or replied/bounced/opted-out leads are never approved


GenerateDraftsOut = JobOut
