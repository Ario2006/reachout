"""Paths and constants. Everything user-owned lives under ~/Reachout/ (or $REACHOUT_HOME)."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Reachout"
APP_VERSION = "0.2.0"
KEYRING_SERVICE = "Reachout"

# Whatever the settings say, we never send more than this per day.
HARD_DAILY_CEILING = 100

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993

DEFAULT_MODEL = "qwen2.5:3b-instruct"
FALLBACK_MODELS = ["llama3.2:3b", "qwen3:4b"]


def home() -> Path:
    return Path(os.environ.get("REACHOUT_HOME") or Path.home() / "Reachout").expanduser()


def db_path() -> Path:
    return home() / "reachout.db"


def exports_dir() -> Path:
    return home() / "exports"


def logs_dir() -> Path:
    return home() / "logs"


def ensure_dirs() -> None:
    for d in (home(), exports_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)
