"""Follow-up plan and controls (milestone 6)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import EngineDep
from backend.api.schemas_inbox import FollowupPlan, FollowupSkipIn
from backend.core import followups

router = APIRouter(tags=["followups"])


@router.get("/campaigns/{campaign_id}/followups", response_model=FollowupPlan)
def followup_plan(campaign_id: int, engine: EngineDep) -> dict:
    out = followups.plan(campaign_id, engine)
    if out is None:
        raise HTTPException(404, "Campaign not found")
    return out


@router.post("/campaigns/{campaign_id}/followups/skip", status_code=204)
def skip_followup(campaign_id: int, body: FollowupSkipIn, engine: EngineDep) -> None:
    try:
        followups.skip(campaign_id, body.lead_id, body.kind, body.reason, engine)
    except followups.FollowupNotFound as e:
        raise HTTPException(404, str(e)) from e
    except followups.FollowupConflict as e:
        raise HTTPException(409, str(e)) from e
