"""Gmail App Password storage in the macOS Keychain (via `keyring`). Never touches disk or the DB."""
from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

from backend.core.config import KEYRING_SERVICE


def _account(email: str) -> str:
    return email.strip().lower()


def set_app_password(email: str, password: str) -> None:
    if not email.strip():
        raise ValueError("email is required")
    # Google shows App Passwords as four groups separated by spaces; they work without.
    keyring.set_password(KEYRING_SERVICE, _account(email), password.replace(" ", ""))


def get_app_password(email: str) -> str | None:
    if not email.strip():
        return None
    return keyring.get_password(KEYRING_SERVICE, _account(email))


def has_app_password(email: str) -> bool:
    return bool(get_app_password(email))


def delete_app_password(email: str) -> bool:
    try:
        keyring.delete_password(KEYRING_SERVICE, _account(email))
        return True
    except PasswordDeleteError:
        return False
