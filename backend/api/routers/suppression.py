from __future__ import annotations

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, HTTPException, Query

from backend.api.deps import EngineDep
from backend.api.schemas import SuppressionAdd, SuppressionEntry
from backend.core import db

router = APIRouter(prefix="/suppression", tags=["suppression"])


def _entry(row: dict) -> SuppressionEntry:
    return SuppressionEntry(**row, removable=row["reason"] == db.MANUAL_REASON)


@router.get("", response_model=list[SuppressionEntry])
def list_suppression(engine: EngineDep, q: str = "") -> list[SuppressionEntry]:
    q = q.strip().lower()
    return [_entry(r) for r in db.list_suppression(engine) if q in r["email"]]


@router.post("", response_model=SuppressionEntry, status_code=201)
def add_suppression(body: SuppressionAdd, engine: EngineDep) -> SuppressionEntry:
    email = str(body.email).lower()
    db.add_suppression(email, db.MANUAL_REASON, engine)
    return _entry(next(r for r in db.list_suppression(engine) if r["email"] == email))


@router.delete("", status_code=204)
def remove_suppression(engine: EngineDep, email: str = Query(...),
                       confirm: str = Query(..., description="Must equal `email` (typed confirmation)")) -> None:
    """Only entries added by hand can be removed; bounce and opt-out entries are permanent."""
    try:
        norm = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise HTTPException(422, "Invalid email address") from None
    if confirm.strip().lower() != norm:
        raise HTTPException(400, "Confirmation does not match the email address")
    result = db.remove_manual_suppression(norm, engine)
    if result == "not_found":
        raise HTTPException(404, "Not on the suppression list")
    if result == "protected":
        raise HTTPException(
            403, "This address was suppressed automatically (bounce or opt-out) and cannot be removed"
        )
