"""Structured log with correlation ids: one JSON object per line in logs/pipeline.jsonl.

A lead's `trace_id` ties together the search, the pages, the decisions and (later) the email, so
"why was this found / emailed?" is answerable from the log or the lead drawer. Never raises."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid

from backend.core import config

MAX_BYTES = 5_000_000
_lock = threading.Lock()


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def log_event(kind: str, *, trace_id: str = "", run_id: int | None = None, **fields: object) -> None:
    try:
        path = config.logs_dir() / "pipeline.jsonl"
        line = json.dumps({"ts": round(time.time(), 3), "kind": kind, "trace_id": trace_id or None, "run_id": run_id, **fields},
                          default=str, ensure_ascii=False)
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                os.replace(path, path.with_suffix(".jsonl.1"))
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:  # noqa: BLE001 - logging must never break the pipeline
        logging.getLogger("reachout.slog").debug("could not write the pipeline log", exc_info=True)
