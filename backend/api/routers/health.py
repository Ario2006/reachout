from __future__ import annotations

from fastapi import APIRouter

from backend.api.deps import EngineDep
from backend.api.schemas import HealthOut, WorkerOut
from backend.core import config, db

router = APIRouter(tags=["health"])


def worker_out(engine) -> WorkerOut:
    alive, pid, age = db.worker_status(engine=engine)
    return WorkerOut(alive=alive, pid=pid, age_s=round(age, 1) if age is not None else None)


@router.get("/health", response_model=HealthOut)
def health(engine: EngineDep) -> HealthOut:
    return HealthOut(status="ok", version=config.APP_VERSION,
                     schema_version=db.schema_version(engine), worker=worker_out(engine))
