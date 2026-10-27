"""Inbox threads, lead status actions, check-now (milestone 5). Owner: task D."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.deps import EngineDep
from backend.api.schemas_inbox import (CheckNowOut, InboxFilter, InboxStatus, LeadStatusIn, ThreadDetail, ThreadLead,
                                       ThreadsPage)
from backend.api.stubs import not_built

router = APIRouter(tags=["inbox"])
OWNER = "milestone 5: tracking"


@router.get("/threads", response_model=ThreadsPage)
def list_threads(engine: EngineDep, filter: InboxFilter = "all", q: str = "", campaign_id: int | None = None,
                 offset: int = 0, limit: int = 100) -> ThreadsPage:
    raise not_built(OWNER)


@router.get("/threads/{lead_id}", response_model=ThreadDetail)
def get_thread(lead_id: int, engine: EngineDep) -> ThreadDetail:
    """Opening a thread marks its incoming events read."""
    raise not_built(OWNER)


@router.post("/inbox/check-now", response_model=CheckNowOut, status_code=202)
def check_now(engine: EngineDep) -> dict:
    """Starts the `inbox_check` job (the same code the 10-minute poller runs)."""
    raise not_built(OWNER)


@router.get("/inbox/status", response_model=InboxStatus)
def inbox_status(engine: EngineDep) -> InboxStatus:
    raise not_built(OWNER)


@router.post("/leads/{lead_id}/status", response_model=ThreadLead)
def set_lead_status(lead_id: int, body: LeadStatusIn, engine: EngineDep) -> ThreadLead:
    """Manual status / outcome / notes. Moving a lead to a blocking status (closed, opted_out, ...) stops all
    further emails to it; it can never un-suppress an address."""
    raise not_built(OWNER)


@router.post("/events/{event_id}/confirm-opt-out", status_code=204)
def confirm_opt_out(event_id: int, engine: EngineDep) -> None:
    """The user checked the automatic opt-out detection and agrees. The address is already suppressed."""
    raise not_built(OWNER)
