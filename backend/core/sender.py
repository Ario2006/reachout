"""Sender worker (milestone 4, owner C): picks eligible queued messages through `outbox.next_eligible`, claims, sends
through `services.get_services().mailer()`, and records the outcome. Contract stub: no-op until C replaces it."""
from __future__ import annotations

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy import Engine

from backend.core.jobs import JobContext


def register(sched: BaseScheduler, engine: Engine) -> None:
    """Adds the send-tick job to the worker's scheduler."""


def run_campaign_dry_run(ctx: JobContext, payload: dict) -> dict:
    """Job `campaign_dry_run`. payload: {"campaign_id": int}. Result: {"lines": [...], "would_send": n, "blocked": m}."""
    raise NotImplementedError("campaign_dry_run: milestone 4")  # pragma: no cover - contract stub
