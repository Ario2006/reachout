"""Campaigns and sending. CRUD is real (Phase 0); the rest is owner C's (milestone 4)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import EngineDep
from backend.api.schemas import JobOut
from backend.api.schemas_campaigns import (CampaignIn, CampaignOut, CampaignStats, ConfirmMessageIn, DryRunOut,
                                           MessageOut, MessageState, Readiness, SendingStatus, TestSendOut)
from backend.api.stubs import not_built
from backend.core import campaigns

router = APIRouter(tags=["campaigns"])
OWNER = "milestone 4: sending"


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


# ---- owner C -----------------------------------------------------------------------------------
@router.get("/campaigns/{campaign_id}/readiness", response_model=Readiness)
def readiness(campaign_id: int, engine: EngineDep) -> Readiness:
    raise not_built(OWNER)


@router.get("/campaigns/{campaign_id}/stats", response_model=CampaignStats)
def stats(campaign_id: int, engine: EngineDep) -> CampaignStats:
    raise not_built(OWNER)


@router.post("/campaigns/{campaign_id}/test-send", response_model=TestSendOut)
def test_send(campaign_id: int, engine: EngineDep) -> TestSendOut:
    """Sends the first approved draft to the user's own Gmail address, with the footer, through the real mailer."""
    raise not_built(OWNER)


@router.post("/campaigns/{campaign_id}/dry-run", response_model=DryRunOut, status_code=202)
def dry_run(campaign_id: int, engine: EngineDep) -> dict:
    """Job that runs every send-safety check for each approved draft and logs the decision. Sends nothing."""
    raise not_built(OWNER)


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignOut)
def start(campaign_id: int, engine: EngineDep) -> dict:
    raise not_built(OWNER)


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
def pause(campaign_id: int, engine: EngineDep) -> dict:
    raise not_built(OWNER)


@router.post("/campaigns/{campaign_id}/resume", response_model=CampaignOut)
def resume(campaign_id: int, engine: EngineDep) -> dict:
    raise not_built(OWNER)


@router.get("/campaigns/{campaign_id}/messages", response_model=list[MessageOut])
def messages(campaign_id: int, engine: EngineDep, state: MessageState | None = None) -> list[dict]:
    raise not_built(OWNER)


@router.post("/messages/{message_id}/confirm", response_model=MessageOut)
def confirm_message(message_id: int, body: ConfirmMessageIn, engine: EngineDep) -> dict:
    """needs_confirmation -> sent or failed, by the user's word. Never resends."""
    raise not_built(OWNER)


@router.post("/messages/{message_id}/retry", response_model=MessageOut)
def retry_message(message_id: int, engine: EngineDep) -> dict:
    """failed -> queued (only failures where the server never accepted the message)."""
    raise not_built(OWNER)


@router.get("/sending/status", response_model=SendingStatus)
def sending_status(engine: EngineDep) -> SendingStatus:
    raise not_built(OWNER)


@router.post("/sending/pause-all", response_model=SendingStatus)
def pause_all(engine: EngineDep) -> SendingStatus:
    """Also called by Electron on Quit so sending stops cleanly before the processes exit."""
    raise not_built(OWNER)


@router.post("/sending/resume-all", response_model=SendingStatus)
def resume_all(engine: EngineDep) -> SendingStatus:
    raise not_built(OWNER)
