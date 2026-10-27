"""Inbox threads, lead status actions, check-now (milestone 5)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import EngineDep
from backend.api.schemas_inbox import (CheckNowOut, InboxFilter, InboxStatus, LeadStatusIn, ThreadDetail, ThreadLead,
                                       ThreadsPage)
from backend.core import inbox_poller, jobs, threads

router = APIRouter(tags=["inbox"])


@router.get("/threads", response_model=ThreadsPage)
def list_threads(engine: EngineDep, filter: InboxFilter = "all", q: str = "", campaign_id: int | None = None,
                 offset: int = 0, limit: int = 100) -> dict:
    return threads.list_threads(engine, filter, q, campaign_id, offset, limit)


@router.get("/threads/{lead_id}", response_model=ThreadDetail)
def get_thread(lead_id: int, engine: EngineDep) -> dict:
    """Opening a thread marks its incoming events read."""
    found = threads.get_thread(engine, lead_id)
    if found is None:
        raise HTTPException(404, "Lead not found")
    return found


@router.post("/inbox/check-now", response_model=CheckNowOut, status_code=202)
def check_now(engine: EngineDep) -> dict:
    """Starts the `inbox_check` job (the same code the poller runs). A check that is already queued or running is
    returned instead of starting a second one."""
    return inbox_poller.active_check_job(engine) or jobs.create_job("inbox_check", {}, engine)


@router.get("/inbox/status", response_model=InboxStatus)
def inbox_status(engine: EngineDep) -> dict:
    return inbox_poller.status(engine)


@router.post("/leads/{lead_id}/status", response_model=ThreadLead)
def set_lead_status(lead_id: int, body: LeadStatusIn, engine: EngineDep) -> dict:
    """Manual status / outcome / notes. Moving a lead to a blocking status (closed, opted_out, ...) stops all
    further emails to it; it can never un-suppress an address."""
    try:
        lead = threads.set_status(engine, lead_id, body.status.value if body.status else None, body.outcome, body.notes)
    except threads.StatusRefused as e:
        raise HTTPException(e.code, e.message) from None
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return lead


@router.post("/events/{event_id}/confirm-opt-out", status_code=204)
def confirm_opt_out(event_id: int, engine: EngineDep) -> None:
    """The user checked the automatic opt-out detection and agrees. The address is already suppressed."""
    if not threads.confirm_opt_out(engine, event_id):
        raise HTTPException(404, "Opt-out event not found")
