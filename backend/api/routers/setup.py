from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException

from backend.api.deps import EngineDep
from backend.api.routers.health import worker_out
from backend.api.schemas import (CheckOut, CompleteSetup, GmailStatus, GmailTestResult, JobOut,
                                 ModelsOut, PullModelRequest, SetupStatus)
from backend.core import db, health, jobs, llm, secrets

router = APIRouter(prefix="/setup", tags=["setup"])
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]*(:[A-Za-z0-9._\-]+)?$")


def _out(check_id: str, c: health.Check) -> CheckOut:
    return CheckOut(id=check_id, name=c.name, status=c.status, detail=c.detail, hint=c.hint)


def _local_checks(model: str) -> list[CheckOut]:
    return [
        _out("data_dir", health.check_data_dir()),
        _out("ollama", health.check_ollama()),
        _out("model", health.check_model(model)),
        _out("memory", health.check_memory()),
    ]


def _skip_wizard() -> bool:
    """Test-only escape hatch (Playwright smoke test)."""
    return os.environ.get("REACHOUT_SKIP_WIZARD") == "1"


@router.get("/status", response_model=SetupStatus)
def status(engine: EngineDep) -> SetupStatus:
    s = db.load_settings(engine)
    active = jobs.list_jobs(kind="pull_model", active_only=True, limit=1, engine=engine)
    return SetupStatus(
        first_run_complete=s.first_run_complete or _skip_wizard(),
        legal_acknowledged=s.legal_acknowledged or _skip_wizard(),
        display_name=s.display_name,
        footer_text=s.footer_text,
        model=s.ollama_model,
        checks=_local_checks(s.ollama_model),
        gmail=GmailStatus(address=s.gmail_address,
                          password_set=secrets.has_app_password(s.gmail_address)),
        worker=worker_out(engine),
        pull_job=active[0] if active else None,
    )


@router.get("/models", response_model=ModelsOut)
def models() -> ModelsOut:
    """Models installed in Ollama (for the Settings dropdown)."""
    if not llm.is_running():
        return ModelsOut(ollama_running=False, installed=[])
    try:
        return ModelsOut(ollama_running=True, installed=sorted(llm.list_models()))
    except Exception:  # noqa: BLE001 - Ollama hiccup: report as not running rather than 500
        return ModelsOut(ollama_running=False, installed=[])


@router.post("/pull-model", response_model=JobOut, status_code=202)
def pull_model(engine: EngineDep, body: PullModelRequest | None = None) -> dict:
    """Queue a model pull (runs in the worker). Progress arrives as `job` events on /events."""
    model = ((body.model if body else None) or db.load_settings(engine).ollama_model).strip()
    if not _MODEL_RE.match(model):
        raise HTTPException(422, "Invalid model name")
    for j in jobs.list_jobs(kind="pull_model", active_only=True, engine=engine):
        if j["payload"].get("model") == model:
            return j  # already queued or running: don't start a duplicate
    return jobs.create_job("pull_model", {"model": model}, engine)


@router.post("/test-gmail", response_model=GmailTestResult)
def test_gmail(engine: EngineDep) -> GmailTestResult:
    """Logs in to Gmail SMTP and IMAP with the saved address and Keychain password."""
    addr = db.load_settings(engine).gmail_address
    pw = secrets.get_app_password(addr)
    return GmailTestResult(smtp=_out("smtp", health.check_smtp(addr, pw)),
                           imap=_out("imap", health.check_imap(addr, pw)))


@router.post("/complete", response_model=SetupStatus)
def complete(body: CompleteSetup, engine: EngineDep) -> SetupStatus:
    if not body.legal_acknowledged:
        raise HTTPException(422, "The legal notice must be acknowledged")
    s = db.load_settings(engine)
    if not _skip_wizard():
        failing = [c for c in _local_checks(s.ollama_model)[:3] if c.status != "ok"]
        if failing:
            raise HTTPException(409, f"Setup checks are failing: {', '.join(c.name for c in failing)}")
    db.save_settings(
        s.model_copy(update={
            "display_name": body.display_name.strip(),
            "footer_text": body.footer_text.strip(),
            "legal_acknowledged": True,
            "first_run_complete": True,
        }),
        engine,
    )
    return status(engine)
