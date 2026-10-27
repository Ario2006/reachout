"""IMAP poller (milestone 5, owner D). Contract stub: no-op until D replaces it."""
from __future__ import annotations

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy import Engine

from backend.core.jobs import JobContext


def register(sched: BaseScheduler, engine: Engine) -> None:
    """Adds the poll job (every `imap_poll_minutes`) to the worker's scheduler."""


def run_inbox_check(ctx: JobContext, payload: dict) -> dict:
    """Job `inbox_check`. Result: {"new_replies": n, "bounces": n, "auto_replies": n, "opt_outs": n}."""
    raise NotImplementedError("inbox_check: milestone 5")  # pragma: no cover - contract stub
