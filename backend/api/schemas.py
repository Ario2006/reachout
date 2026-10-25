"""Request/response models. These drive the generated OpenAPI schema and TypeScript client."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, create_model

from backend.core.models import AppSettings

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
