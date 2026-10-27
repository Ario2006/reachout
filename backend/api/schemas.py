"""Request/response models. These drive the generated OpenAPI schema and TypeScript client."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, create_model

from backend.core.models import AppSettings, EmailConfidence, LeadStatus, Mode
from backend.core.planner import Plan

# Fields the generic settings update must never touch.
_PROTECTED = {"gmail_address", "first_run_complete", "legal_acknowledged"}

SettingsUpdate = create_model(
    "SettingsUpdate",
    __config__=ConfigDict(extra="forbid"),
    __doc__="Partial update of editable settings. Omitted fields keep their value.",
    **{
        name: (Optional[f.annotation], None)
        for name, f in AppSettings.model_fields.items()
        if name not in _PROTECTED
    },
)


class Limits(BaseModel):
    hard_daily_ceiling: int


class SettingsResponse(BaseModel):
    settings: AppSettings
    gmail_password_set: bool
    warnings: list[str]
    notices: list[str] = []
    limits: Limits


class GmailStatus(BaseModel):
    address: str
    password_set: bool


class GmailUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: EmailStr
    # Optional: omit to keep the password already in the Keychain. Never returned by any endpoint.
    app_password: Optional[str] = Field(default=None, repr=False)


class CheckOut(BaseModel):
    id: str
    name: str
    status: str  # ok | warn | fail
    detail: str
    hint: str = ""


class WorkerOut(BaseModel):
    alive: bool
    pid: Optional[int] = None
    age_s: Optional[float] = None


class JobOut(BaseModel):
    id: int
    kind: str
    state: str
    progress: int
    total: int
    message: Optional[str] = None
    error: Optional[str] = None
    payload: dict = {}
    result: Optional[dict] = None
    cancel_requested: bool
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class HealthOut(BaseModel):
    status: str
    version: str
    schema_version: int
    worker: WorkerOut


class SetupStatus(BaseModel):
    first_run_complete: bool
    legal_acknowledged: bool
    display_name: str
    footer_text: str
    model: str
    checks: list[CheckOut]
    gmail: GmailStatus
    worker: WorkerOut
    pull_job: Optional[JobOut] = None


class ModelsOut(BaseModel):
    ollama_running: bool
    installed: list[str]


class PullModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Optional[str] = None


class GmailTestResult(BaseModel):
    smtp: CheckOut
    imap: CheckOut


class CompleteSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1)
    footer_text: str = Field(min_length=1)
    legal_acknowledged: bool


class SuppressionEntry(BaseModel):
    email: str
    reason: Optional[str] = None
    added_at: str
    removable: bool


class SuppressionAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


# ---- discovery ------------------------------------------------------------------------------

class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=2000)
    mode: Optional[Mode] = None


class PlanResponse(BaseModel):
    plan: Plan
    source: Literal["llm", "fallback"]
    note: str = ""


class RunOptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recrawl: bool = False
    use_overpass: bool = False
    expand_lists: bool = True
    max_domains: Optional[int] = Field(default=None, ge=1, le=300)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_text: str = Field(min_length=1, max_length=2000)
    plan: Plan
    options: RunOptionsIn = RunOptionsIn()


class RunOut(BaseModel):
    id: int
    request_text: str
    mode: Optional[str] = None
    state: str
    stage: Optional[str] = None
    stats: dict[str, object] = {}
    started_at: str
    finished_at: Optional[str] = None
    pages_fetched: int = 0
    leads_found: int = 0
    error: Optional[str] = None
    job_id: Optional[int] = None
    plan: Plan


class RunDomainOut(BaseModel):
    domain: str
    url: Optional[str] = None
    state: str
    pages: int = 0
    emails: int = 0
    leads: int = 0
    note: Optional[str] = None


class RunLogLine(BaseModel):
    id: int
    message: str
    created_at: str


class RunDetail(RunOut):
    domains: list[RunDomainOut] = []
    log: list[RunLogLine] = []


# ---- leads ----------------------------------------------------------------------------------

class LeadOut(BaseModel):
    id: int
    name: Optional[str] = None
    email: str
    role: Optional[str] = None
    company: Optional[str] = None
    domain: Optional[str] = None
    source_url: Optional[str] = None
    email_confidence: EmailConfidence
    mode: Optional[Mode] = None
    status: LeadStatus
    notes: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    search_run_id: Optional[int] = None
    score: Optional[int] = None


class LeadsPage(BaseModel):
    items: list[LeadOut]
    total: int


class TimelineItem(BaseModel):
    type: str
    detail: Optional[str] = None
    at: Optional[str] = None


class LeadRun(BaseModel):
    id: int
    request_text: str
    started_at: str


class ScoreReason(BaseModel):
    label: str
    points: int
    detail: str


class LeadSource(BaseModel):
    source_url: str
    page_tier: Optional[str] = None
    page_type: Optional[str] = None
    fetched_at: Optional[float] = None
    context_snippet: Optional[str] = None
    first_seen: Optional[str] = None
    run_id: Optional[int] = None


class TraceItem(BaseModel):
    at: str
    kind: str
    message: str


class LeadDetail(LeadOut):
    context_snippet: Optional[str] = None
    run: Optional[LeadRun] = None
    timeline: list[TimelineItem] = []
    score_reasons: list[ScoreReason] = []
    sources: list[LeadSource] = []
    trace: list[TraceItem] = []


class LeadPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, max_length=200)
    role: Optional[str] = Field(default=None, max_length=200)
    company: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=5000)
    status: Optional[LeadStatus] = None


class BulkResult(BaseModel):
    affected: int
    skipped: int


class ImportIssue(BaseModel):
    row: int
    email: str
    reason: str


class ImportReportOut(BaseModel):
    total: int
    valid: int
    inserted: int
    duplicates: int
    suppressed: int
    invalid: int
    no_mx: int
    issues: list[ImportIssue]
