"""Campaigns and sending: CRUD plus the run controls (readiness, test send, dry run, start, pause, resume, messages, pause-all)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import EngineDep
from backend.api.schemas import JobOut
from backend.api.schemas_campaigns import (CampaignIn, CampaignOut, CampaignStats, ConfirmMessageIn, DryRunOut,
                                           MessageOut, MessageState, Readiness, SendingStatus, TestSendOut)
from backend.core import campaigns, jobs

router = APIRouter(tags=["campaigns"])


@router.get("/campaigns", response_model=list[CampaignOut])
def list_campaigns(engine: EngineDep) -> list[dict]:
    return campaigns.list_all(engine)


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
def create_campaign(body: CampaignIn, engine: EngineDep) -> dict:
    return campaigns.create(body.model_dump(), engine)


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, engine: EngineDep) -> dict:
    c = campaigns.get(campaign_id, engine)
    if c is None:
        raise HTTPException(404, "Campaign not found")
    return c


@router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: int, body: CampaignIn, engine: EngineDep) -> dict:
    try:
        c = campaigns.update(campaign_id, body.model_dump(), engine)
    except campaigns.CampaignLocked as e:
        raise HTTPException(409, str(e)) from e
    if c is None:
        raise HTTPException(404, "Campaign not found")
    return c


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: int, engine: EngineDep) -> None:
    try:
        if not campaigns.delete(campaign_id, engine):
            raise HTTPException(404, "Campaign not found")
    except campaigns.CampaignLocked as e:
        raise HTTPException(409, str(e)) from e


# ---- run controls ------------------------------------------------------------------------------
def _found(x):
    if x is None:
        raise HTTPException(404, "Not found")
    return x


def _control(fn, *a):
    try:
        return _found(fn(*a))
    except campaigns.ControlRefused as e:
        raise HTTPException(409, str(e)) from e


@router.get("/campaigns/{campaign_id}/readiness", response_model=Readiness)
def readiness(campaign_id: int, engine: EngineDep) -> dict:
    return _found(campaigns.readiness(campaign_id, engine))


@router.get("/campaigns/{campaign_id}/stats", response_model=CampaignStats)
def stats(campaign_id: int, engine: EngineDep) -> dict:
    return _found(campaigns.stats(campaign_id, engine))


@router.post("/campaigns/{campaign_id}/test-send", response_model=TestSendOut)
def test_send(campaign_id: int, engine: EngineDep) -> dict:
    """Sends the first approved draft to the user's own Gmail address, with the footer, through the real mailer."""
    return _control(campaigns.test_send, campaign_id, engine)


@router.post("/campaigns/{campaign_id}/dry-run", response_model=DryRunOut, status_code=202)
def dry_run(campaign_id: int, engine: EngineDep) -> dict:
    """Job that runs every send-safety check for each approved draft and logs the decision. Sends nothing."""
    _found(campaigns.get(campaign_id, engine))
    return jobs.create_job("campaign_dry_run", {"campaign_id": campaign_id}, engine)


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignOut)
def start(campaign_id: int, engine: EngineDep) -> dict:
    return _control(campaigns.start, campaign_id, engine)


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
def pause(campaign_id: int, engine: EngineDep) -> dict:
    return _found(campaigns.pause(campaign_id, "user", None, engine))


@router.post("/campaigns/{campaign_id}/resume", response_model=CampaignOut)
def resume(campaign_id: int, engine: EngineDep) -> dict:
    return _control(campaigns.resume, campaign_id, engine)


@router.get("/campaigns/{campaign_id}/messages", response_model=list[MessageOut])
def messages(campaign_id: int, engine: EngineDep, state: MessageState | None = None) -> list[dict]:
    _found(campaigns.get(campaign_id, engine))
    return campaigns.list_messages(campaign_id, state, engine)


@router.post("/messages/{message_id}/confirm", response_model=MessageOut)
def confirm_message(message_id: int, body: ConfirmMessageIn, engine: EngineDep) -> dict:
    """needs_confirmation -> sent (it went out) or queued (it did not). Never resends by itself."""
    return _control(campaigns.confirm_message, message_id, body.was_sent, engine)


@router.post("/messages/{message_id}/retry", response_model=MessageOut)
def retry_message(message_id: int, engine: EngineDep) -> dict:
    """failed -> queued (only failures where the server never accepted the message)."""
    return _control(campaigns.retry_message, message_id, engine)


@router.get("/sending/status", response_model=SendingStatus)
def sending_status(engine: EngineDep) -> dict:
    return campaigns.sending_status(engine)


@router.post("/sending/pause-all", response_model=SendingStatus)
def pause_all(engine: EngineDep) -> dict:
    """Also called by Electron on Quit so sending stops cleanly before the processes exit."""
    return campaigns.pause_all(engine)


@router.post("/sending/resume-all", response_model=SendingStatus)
def resume_all(engine: EngineDep) -> dict:
    return campaigns.resume_all(engine)
