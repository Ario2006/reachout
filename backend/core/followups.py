"""Follow-up scheduler (milestone 6, owner E). Contract stub: no-op until E replaces it."""
from __future__ import annotations

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy import Engine


def register(sched: BaseScheduler, engine: Engine) -> None:
    """Adds the follow-up tick: enqueues due follow-ups into `messages` (kind followup_N, not_before, threading
    headers) for leads with no reply, bounce, opt-out or blocking status."""
