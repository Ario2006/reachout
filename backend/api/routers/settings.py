from __future__ import annotations

from fastapi import APIRouter, HTTPException
from keyring.errors import KeyringError
from pydantic import ValidationError

from backend.api.deps import EngineDep
from backend.api.schemas import GmailStatus, GmailUpdate, Limits, SettingsResponse, SettingsUpdate
from backend.core import config, db, secrets
from backend.core.models import AppSettings, risk_warnings

router = APIRouter(prefix="/settings", tags=["settings"])


def _response(s: AppSettings, notices: list[str] | None = None) -> SettingsResponse:
    return SettingsResponse(
        settings=s,
        gmail_password_set=secrets.has_app_password(s.gmail_address),
        warnings=risk_warnings(s),
        notices=notices or [],
        limits=Limits(hard_daily_ceiling=config.HARD_DAILY_CEILING),
    )


def validation_422(e: ValidationError) -> HTTPException:
    detail = [
        {"loc": ["body", *map(str, err["loc"])], "msg": err["msg"].removeprefix("Value error, ")}
        for err in e.errors()
    ]
    return HTTPException(422, detail)


@router.get("", response_model=SettingsResponse)
def get_settings(engine: EngineDep) -> SettingsResponse:
    return _response(db.load_settings(engine))


@router.put("", response_model=SettingsResponse)
def update_settings(body: SettingsUpdate, engine: EngineDep) -> SettingsResponse:  # type: ignore[valid-type]
    current = db.load_settings(engine)
    changes = body.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
    try:
        new = AppSettings.model_validate(current.model_dump() | changes)
    except ValidationError as e:
        raise validation_422(e) from e
    notices = []
    if changes.get("daily_cap", 0) > config.HARD_DAILY_CEILING:
        notices.append(f"Daily cap limited to the hard ceiling of {config.HARD_DAILY_CEILING}.")
    db.save_settings(new, engine)
    return _response(new, notices)


@router.get("/gmail", response_model=GmailStatus)
def get_gmail(engine: EngineDep) -> GmailStatus:
    addr = db.load_settings(engine).gmail_address
    return GmailStatus(address=addr, password_set=secrets.has_app_password(addr))


@router.put("/gmail", response_model=GmailStatus)
def put_gmail(body: GmailUpdate, engine: EngineDep) -> GmailStatus:
    """Writes the App Password to the macOS Keychain. The password is never stored or echoed."""
    address = str(body.address).strip().lower()
    try:
        if body.app_password:
            secrets.set_app_password(address, body.app_password)
        current = db.load_settings(engine)
        db.save_settings(current.model_copy(update={"gmail_address": address}), engine)
        return GmailStatus(address=address, password_set=secrets.has_app_password(address))
    except KeyringError:
        raise HTTPException(500, "Could not write to the macOS Keychain. Check Keychain Access permissions.") from None


@router.delete("/gmail/password", response_model=GmailStatus)
def delete_gmail_password(engine: EngineDep) -> GmailStatus:
    addr = db.load_settings(engine).gmail_address
    secrets.delete_app_password(addr)
    return GmailStatus(address=addr, password_set=secrets.has_app_password(addr))
