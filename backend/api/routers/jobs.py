from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import EngineDep
from backend.api.schemas import JobOut
from backend.core import jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(engine: EngineDep, kind: str | None = None, active: bool = False,
              limit: int = 50) -> list[dict]:
    return jobs.list_jobs(kind=kind, active_only=active, limit=min(max(limit, 1), 200), engine=engine)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, engine: EngineDep) -> dict:
    job = jobs.get_job(job_id, engine)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, engine: EngineDep) -> dict:
    job = jobs.request_cancel(job_id, engine)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job
