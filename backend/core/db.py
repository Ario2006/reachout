"""SQLite access: engine, versioned-SQL migrations, settings and suppression helpers."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, event, text

from backend.core import config
from backend.core.models import AppSettings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_engines: dict[str, Engine] = {}


def get_engine(path: Path | None = None) -> Engine:
    """One cached engine per DB file. WAL mode lets the UI and worker share the file."""
    path = Path(path) if path else config.db_path()
    key = str(path)
    if key not in _engines:
        path.parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        _engines[key] = eng
    return _engines[key]


def reset_engines() -> None:
    """Dispose cached engines (used by tests when the data dir changes)."""
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()


# ---------------------------------------------------------------- migrations

def _migration_files() -> list[tuple[int, Path]]:
    files = []
    for p in MIGRATIONS_DIR.glob("*.sql"):
        m = re.match(r"^(\d+)_", p.name)
        if m:
            files.append((int(m.group(1)), p))
    return sorted(files)


def migrate(engine: Engine | None = None) -> list[int]:
    """Apply pending migrations in order, each in its own transaction. Returns versions applied."""
    engine = engine or get_engine()
    applied: list[int] = []
    raw = engine.raw_connection()
    try:
        raw.executescript(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);"
        )
        done = {r[0] for r in raw.execute("SELECT version FROM schema_version")}
        for version, path in _migration_files():
            if version in done:
                continue
            script = (
                "BEGIN;\n"
                + path.read_text()
                + f"\nINSERT INTO schema_version (version, applied_at) VALUES ({version}, datetime('now'));"
                + "\nCOMMIT;"
            )
            try:
                raw.executescript(script)
            except Exception:
                if raw.in_transaction:
                    raw.execute("ROLLBACK")
                raise
            applied.append(version)
    finally:
        raw.close()
    return applied


def schema_version(engine: Engine | None = None) -> int:
    engine = engine or get_engine()
    with engine.connect() as c:
        return c.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_version")).scalar_one()


# ----------------------------------------------------------------- settings

def load_settings(engine: Engine | None = None) -> AppSettings:
    """Defaults overlaid with stored values. A corrupt stored value falls back to its default."""
    engine = engine or get_engine()
    with engine.connect() as c:
        rows = c.execute(text("SELECT key, value FROM settings")).all()
    stored: dict = {}
    for k, v in rows:
        if k in AppSettings.model_fields:
            try:
                stored[k] = json.loads(v)
            except (TypeError, json.JSONDecodeError):
                continue
    while True:
        try:
            return AppSettings(**stored)
        except ValidationError as e:
            bad = {err["loc"][0] for err in e.errors() if err["loc"]}
            if not bad & stored.keys():
                # Cross-field problem in stored data: start from defaults.
                return AppSettings()
            for k in bad:
                stored.pop(k, None)


def save_settings(s: AppSettings, engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    rows = [{"k": k, "v": json.dumps(v)} for k, v in s.model_dump(mode="json").items()]
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO settings (key, value) VALUES (:k, :v) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            ),
            rows,
        )


# -------------------------------------------------------------- suppression

def add_suppression(email: str, reason: str, engine: Engine | None = None) -> None:
    """Idempotent. Keeps the first recorded reason."""
    engine = engine or get_engine()
    with engine.begin() as c:
        c.execute(
            text("INSERT OR IGNORE INTO suppression (email, reason) VALUES (:e, :r)"),
            {"e": email.strip().lower(), "r": reason},
        )


def is_suppressed(email: str, engine: Engine | None = None) -> bool:
    engine = engine or get_engine()
    with engine.connect() as c:
        return (
            c.execute(
                text("SELECT 1 FROM suppression WHERE email = :e"), {"e": email.strip().lower()}
            ).first()
            is not None
        )


MANUAL_REASON = "manual"


def remove_manual_suppression(email: str, engine: Engine | None = None) -> str:
    """Remove an entry only if the user added it by hand. Entries created by bounces and
    opt-outs are protected so the UI can never re-enable emailing someone who asked to stop.
    Returns "removed", "not_found" or "protected"."""
    engine = engine or get_engine()
    email = email.strip().lower()
    with engine.begin() as c:
        row = c.execute(text("SELECT reason FROM suppression WHERE email = :e"), {"e": email}).first()
        if row is None:
            return "not_found"
        if row[0] != MANUAL_REASON:
            return "protected"
        c.execute(text("DELETE FROM suppression WHERE email = :e"), {"e": email})
    return "removed"


def list_suppression(engine: Engine | None = None) -> list[dict]:
    engine = engine or get_engine()
    with engine.connect() as c:
        rows = c.execute(
            text("SELECT email, reason, added_at FROM suppression ORDER BY added_at DESC, email")
        ).mappings()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- heartbeat

def touch_heartbeat(pid: int, engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO worker_heartbeat (id, pid, beat_at) VALUES (1, :p, :t) "
                "ON CONFLICT(id) DO UPDATE SET pid = excluded.pid, beat_at = excluded.beat_at"
            ),
            {"p": pid, "t": now},
        )


def worker_status(max_age_s: int = 20, engine: Engine | None = None) -> tuple[bool, int | None, float | None]:
    """(alive, pid, seconds since last heartbeat)."""
    engine = engine or get_engine()
    with engine.connect() as c:
        row = c.execute(text("SELECT pid, beat_at FROM worker_heartbeat WHERE id = 1")).first()
    if not row:
        return False, None, None
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[1])).total_seconds()
    return age <= max_age_s, row[0], age
