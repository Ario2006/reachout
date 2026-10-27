"""Follow-up plan and controls (milestone 6, owner E)."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.deps import EngineDep
from backend.api.schemas_inbox import FollowupPlan, FollowupSkipIn
from backend.api.stubs import not_built

router = APIRouter(tags=["followups"])


@router.get("/campaigns/{campaign_id}/followups", response_model=FollowupPlan)
def followup_plan(campaign_id: int, engine: EngineDep) -> FollowupPlan:
    raise not_built("milestone 6: follow-ups")


@router.post("/campaigns/{campaign_id}/followups/skip", status_code=204)
def skip_followup(campaign_id: int, body: FollowupSkipIn, engine: EngineDep) -> None:
    raise not_built("milestone 6: follow-ups")
