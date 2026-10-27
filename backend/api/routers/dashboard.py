"""Metrics and activity feed (milestone 5). Definitions live in the docstring of backend.core.metrics."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from backend.api.deps import EngineDep
from backend.api.schemas_inbox import ActivityItem, Metrics
from backend.core import metrics as metrics_core

router = APIRouter(tags=["dashboard"])


@router.get("/metrics", response_model=Metrics)
def metrics(engine: EngineDep, campaign_id: int | None = None, range: Literal["7d", "30d", "all"] = "30d") -> dict:
    return metrics_core.compute(engine, campaign_id, range)


@router.get("/activity", response_model=list[ActivityItem])
def activity(engine: EngineDep, limit: int = 30) -> list[dict]:
    return metrics_core.activity(engine, limit)
