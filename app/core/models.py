"""Enums and the validated, non-secret settings model."""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import DEFAULT_MODEL, HARD_DAILY_CEILING

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Mode(str, Enum):
    internship = "internship"
    investor = "investor"
    service = "service"


class LeadStatus(str, Enum):
    new = "new"
    drafted = "drafted"
    approved = "approved"
    queued = "queued"
    sent = "sent"
    replied = "replied"
    bounced = "bounced"
    opted_out = "opted_out"
    auto_reply = "auto_reply"
    closed = "closed"


class EmailConfidence(str, Enum):
    published_personal = "published_personal"
    published_generic = "published_generic"
    guessed = "guessed"


class AppSettings(BaseModel):
    """Everything here is stored in the SQLite `settings` table. Never add secrets."""

    # Identity / email
    gmail_address: str = ""
    display_name: str = ""
    footer_text: str = "If you'd rather not hear from me, just reply 'stop' and I won't email again."

    # LLM
    ollama_model: str = DEFAULT_MODEL
    num_ctx: int = Field(4096, ge=1024, le=8192)
    keep_alive: str = "5m"

    # Sending
    daily_cap: int = Field(25, ge=1)
    delay_min_s: int = Field(60, ge=0)
    delay_max_s: int = Field(180, ge=0)
    window_start: str = "09:00"
    window_end: str = "18:00"
    send_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])  # Mon=0 .. Sun=6
    allow_guessed_emails: bool = False

    # Crawl / search
    max_pages_per_domain: int = Field(6, ge=1, le=20)
    crawl_delay_min_s: float = Field(1.0, ge=0.5)
    crawl_delay_max_s: float = Field(2.0, ge=0.5)
    fetch_timeout_s: int = Field(15, ge=5, le=60)
    search_source: str = Field("ddgs", pattern=r"^(ddgs|searxng)$")
    searxng_url: str = ""

    # State
    first_run_complete: bool = False

    @field_validator("daily_cap")
    @classmethod
    def _clamp_cap(cls, v: int) -> int:
        return min(v, HARD_DAILY_CEILING)

    @field_validator("window_start", "window_end")
    @classmethod
    def _check_time(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("time must be HH:MM (24h)")
        return v

    @field_validator("send_days")
    @classmethod
    def _check_days(cls, v: list[int]) -> list[int]:
        if not v or any(d < 0 or d > 6 for d in v):
            raise ValueError("send_days must be a non-empty list of 0..6")
        return sorted(set(v))

    @field_validator("gmail_address")
    @classmethod
    def _norm_addr(cls, v: str) -> str:
        return v.strip().lower()

    @model_validator(mode="after")
    def _check_ranges(self) -> "AppSettings":
        if self.delay_max_s < self.delay_min_s:
            raise ValueError("delay_max_s must be >= delay_min_s")
        if self.crawl_delay_max_s < self.crawl_delay_min_s:
            raise ValueError("crawl_delay_max_s must be >= crawl_delay_min_s")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.search_source == "searxng" and not self.searxng_url.strip():
            raise ValueError("searxng_url is required when search_source is searxng")
        return self


def risk_warnings(s: AppSettings) -> list[str]:
    """Human-readable warnings for settings that deviate toward riskier than the defaults."""
    out: list[str] = []
    if s.daily_cap > 25:
        out.append(
            f"Daily cap {s.daily_cap} is above the safe default of 25. New or cold Gmail "
            "accounts can be throttled or flagged; ramp up gradually (10, 15, 20, 25, 30)."
        )
    if s.delay_min_s < 60:
        out.append("A minimum delay under 60 s between sends looks automated to Gmail.")
    if s.window_start < "08:00" or s.window_end > "20:00":
        out.append("Sending outside normal working hours reduces replies and looks bot-like.")
    if s.allow_guessed_emails:
        out.append(
            "Guessed addresses are only MX-checked, never verified. They bounce often, and "
            "bounces above 5% auto-pause a campaign."
        )
    if s.crawl_delay_min_s < 1.0:
        out.append("Crawl delay under 1 s per domain is impolite.")
    if s.max_pages_per_domain > 10:
        out.append("Crawling more than 10 pages per domain is slow and heavy on small sites.")
    return out
