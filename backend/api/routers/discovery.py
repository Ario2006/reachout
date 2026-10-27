from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import EngineDep
from backend.api.schemas import PlanRequest, PlanResponse, RunDetail, RunOut, RunRequest
from backend.core import db, jobs, pipeline, planner

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.post("/plan", response_model=PlanResponse)
def make_plan(body: PlanRequest, engine: EngineDep) -> PlanResponse:
    """Turn a plain-English request into an editable plan. Falls back to templates if the model is down."""
    try:
        res = planner.make_plan(body.request, db.load_settings(engine), body.mode)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return PlanResponse(plan=res.plan, source=res.source, note=res.note)  # type: ignore[arg-type]


@router.post("/run", response_model=RunOut, status_code=202)
def start_run(body: RunRequest, engine: EngineDep) -> dict:
    """Queue a discovery run (executed by the worker). Only one run at a time on an 8 GB machine."""
    if not body.plan.search_queries:
        raise HTTPException(422, "The plan needs at least one search query")
    active = jobs.list_jobs(kind="discovery_run", active_only=True, limit=1, engine=engine)
    if active:
        raise HTTPException(409, "A search is already running. Wait for it to finish or cancel it.")
    return pipeline.create_run(body.request_text, body.plan, pipeline.RunOptions(**body.options.model_dump()), engine)


@router.get("/runs", response_model=list[RunOut])
def list_runs(engine: EngineDep, limit: int = 30) -> list[dict]:
    return pipeline.list_runs(min(max(limit, 1), 100), engine)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int, engine: EngineDep) -> dict:
    pipeline.reconcile_runs(engine)
    run = pipeline.get_run(run_id, engine)
    if run is None:
        raise HTTPException(404, "Run not found")
    return {**run, "domains": pipeline.run_domains(run_id, engine), "log": pipeline.run_log(run_id, limit=300, engine=engine)}


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: int, engine: EngineDep) -> dict:
    run = pipeline.get_run(run_id, engine)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run["job_id"]:
        jobs.request_cancel(run["job_id"], engine)
    pipeline.reconcile_runs(engine)  # a queued job is cancelled immediately, so its run must follow
    return pipeline.get_run(run_id, engine) or run
