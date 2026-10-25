"""Event emitter. Core code calls `emit()`; the API tails `event_log` and streams it over SSE.
A table (not an in-memory queue) because the worker and the API are separate processes."""
from __future__ import annotations

import json

from sqlalchemy import Engine, text

from backend.core import db


def emit(type_: str, payload: dict | None = None, engine: Engine | None = None) -> int:
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(
            text("INSERT INTO event_log (type, payload_json) VALUES (:t, :p)"),
            {"t": type_, "p": json.dumps(payload or {})},
        )
        return int(res.lastrowid)


def latest_id(engine: Engine | None = None) -> int:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        return int(c.execute(text("SELECT COALESCE(MAX(id), 0) FROM event_log")).scalar_one())


def read_since(last_id: int, limit: int = 200, engine: Engine | None = None) -> list[dict]:
    """Events with id > last_id, oldest first."""
    engine = engine or db.get_engine()
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, type, payload_json, created_at FROM event_log "
                "WHERE id > :i ORDER BY id LIMIT :n"
            ),
            {"i": last_id, "n": limit},
        ).all()
    return [
        {"id": r[0], "type": r[1], "payload": json.loads(r[2]), "created_at": r[3]} for r in rows
    ]


def prune(keep: int = 2000, engine: Engine | None = None) -> int:
    """Delete all but the newest `keep` events. Returns rows removed."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(
            text("DELETE FROM event_log WHERE id <= (SELECT COALESCE(MAX(id), 0) FROM event_log) - :k"),
            {"k": keep},
        )
        return res.rowcount
