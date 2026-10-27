"""Metrics and activity feed (milestone 5, owner D)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from backend.api.deps import EngineDep
from backend.api.schemas_inbox import ActivityItem, Metrics
from backend.api.stubs import not_built

router = APIRouter(tags=["dashboard"])


@router.get("/metrics", response_model=Metrics)
def metrics(engine: EngineDep, campaign_id: int | None = None, range: Literal["7d", "30d", "all"] = "30d") -> Metrics:
    raise not_built("milestone 5: tracking")


@router.get("/activity", response_model=list[ActivityItem])
def activity(engine: EngineDep, limit: int = 30) -> list[ActivityItem]:
    raise not_built("milestone 5: tracking")
