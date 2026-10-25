"""Job records in SQLite. The API creates jobs; the worker claims and runs them; every state or
progress change is emitted as a `job` event so the UI updates live."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import Engine, text

from backend.core import db, events

log = logging.getLogger("reachout.jobs")

TERMINAL = ("succeeded", "failed", "cancelled")
_COLS = (
    "id, kind, state, progress, total, message, error, payload_json, result_json, "
    "cancel_requested, created_at, started_at, finished_at"
)
Handler = Callable[["JobContext", dict], dict | None]


class JobCancelled(Exception):
    """Raised inside a handler (via ctx.raise_if_cancelled) to stop cooperatively."""


def _row(r) -> dict:
    d = dict(r._mapping)
    d["payload"] = json.loads(d.pop("payload_json") or "{}")
    raw_result = d.pop("result_json")
    d["result"] = json.loads(raw_result) if raw_result else None
    d["cancel_requested"] = bool(d["cancel_requested"])
    return d


def _public(job: dict) -> dict:
    """The slice of a job that goes into events (no payload: it may hold user text)."""
    return {k: job[k] for k in ("id", "kind", "state", "progress", "total", "message", "error")}


def get_job(job_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        r = c.execute(text(f"SELECT {_COLS} FROM jobs WHERE id = :i"), {"i": job_id}).first()
    return _row(r) if r else None


def list_jobs(kind: str | None = None, active_only: bool = False, limit: int = 50,
              engine: Engine | None = None) -> list[dict]:
    engine = engine or db.get_engine()
    where, params = [], {"n": limit}
    if kind:
        where.append("kind = :k")
        params["k"] = kind
    if active_only:
        where.append("state IN ('queued', 'running')")
    sql = f"SELECT {_COLS} FROM jobs" + (" WHERE " + " AND ".join(where) if where else "")
    with engine.connect() as c:
        rows = c.execute(text(sql + " ORDER BY id DESC LIMIT :n"), params).all()
    return [_row(r) for r in rows]


def create_job(kind: str, payload: dict | None = None, engine: Engine | None = None) -> dict:
    engine = engine or db.get_engine()
    with engine.begin() as c:
        res = c.execute(
            text("INSERT INTO jobs (kind, payload_json) VALUES (:k, :p)"),
            {"k": kind, "p": json.dumps(payload or {})},
        )
        job_id = int(res.lastrowid)
    job = get_job(job_id, engine)
    events.emit("job", _public(job), engine)
    return job


def claim_next(engine: Engine | None = None) -> dict | None:
    """Atomically move the oldest queued job to running and return it."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        r = c.execute(
            text(
                "UPDATE jobs SET state = 'running', "
                "started_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = (SELECT id FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1) "
                "AND state = 'queued' RETURNING id"
            )
        ).first()
    if not r:
        return None
    job = get_job(r[0], engine)
    events.emit("job", _public(job), engine)
    return job


def _finish(job_id: int, state: str, result: dict | None, error: str | None,
            engine: Engine) -> None:
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE jobs SET state = :s, result_json = :r, error = :e, "
                "finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = :i"
            ),
            {"s": state, "r": json.dumps(result) if result is not None else None,
             "e": error, "i": job_id},
        )
    events.emit("job", _public(get_job(job_id, engine)), engine)


def request_cancel(job_id: int, engine: Engine | None = None) -> dict | None:
    """Queued jobs are cancelled immediately; running jobs stop at their next checkpoint."""
    engine = engine or db.get_engine()
    job = get_job(job_id, engine)
    if job is None or job["state"] in TERMINAL:
        return job
    if job["state"] == "queued":
        with engine.begin() as c:
            c.execute(
                text(
                    "UPDATE jobs SET state = 'cancelled', cancel_requested = 1, "
                    "finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = :i AND state = 'queued'"
                ),
                {"i": job_id},
            )
    else:
        with engine.begin() as c:
            c.execute(text("UPDATE jobs SET cancel_requested = 1 WHERE id = :i"), {"i": job_id})
    job = get_job(job_id, engine)
    events.emit("job", _public(job), engine)
    return job


def recover_stale(engine: Engine | None = None) -> int:
    """Jobs left 'running' by a crashed worker can never finish: fail them."""
    engine = engine or db.get_engine()
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(text("SELECT id FROM jobs WHERE state = 'running'"))]
        c.execute(
            text(
                "UPDATE jobs SET state = 'failed', error = 'Worker restarted while running', "
                "finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE state = 'running'"
            )
        )
    for i in ids:
        events.emit("job", _public(get_job(i, engine)), engine)
    return len(ids)


class JobContext:
    """Passed to handlers: report progress and check for cancellation."""

    def __init__(self, job_id: int, engine: Engine, min_interval_s: float = 0.25):
        self.job_id, self.engine = job_id, engine
        self._min = min_interval_s
        self._last = 0.0

    def progress(self, done: int, total: int | None = None, message: str | None = None,
                 force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < self._min:
            return
        self._last = now
        sets, params = ["progress = :p"], {"p": done, "i": self.job_id}
        if total is not None:
            sets.append("total = :t")
            params["t"] = total
        if message is not None:
            sets.append("message = :m")
            params["m"] = message
        with self.engine.begin() as c:
            c.execute(text(f"UPDATE jobs SET {', '.join(sets)} WHERE id = :i"), params)
        events.emit("job", _public(get_job(self.job_id, self.engine)), self.engine)

    def cancelled(self) -> bool:
        with self.engine.connect() as c:
            return bool(
                c.execute(text("SELECT cancel_requested FROM jobs WHERE id = :i"),
                          {"i": self.job_id}).scalar()
            )

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled()


def run_job(job: dict, handlers: dict[str, Handler], engine: Engine) -> None:
    """Execute one claimed job and record its outcome. Never raises."""
    ctx = JobContext(job["id"], engine)
    handler = handlers.get(job["kind"])
    try:
        if handler is None:
            raise RuntimeError(f"No handler for job kind {job['kind']!r}")
        result = handler(ctx, job["payload"])
        _finish(job["id"], "succeeded", result, None, engine)
    except JobCancelled:
        _finish(job["id"], "cancelled", None, None, engine)
    except Exception as e:  # noqa: BLE001 - a job failure must never take the worker down
        log.exception("job %s (%s) failed", job["id"], job["kind"])
        _finish(job["id"], "failed", None, str(e) or type(e).__name__, engine)


class JobRunner:
    """Polled by the worker: claims queued jobs and runs them on a small thread pool."""

    def __init__(self, engine: Engine, handlers: dict[str, Handler], max_concurrent: int = 2):
        self.engine, self.handlers, self.max = engine, handlers, max_concurrent
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="job")
        self._running: set[Future] = set()

    def tick(self) -> int:
        """Claim and start as many queued jobs as there is capacity for. Returns started count."""
        self._running = {f for f in self._running if not f.done()}
        started = 0
        while len(self._running) < self.max:
            job = claim_next(self.engine)
            if job is None:
                break
            self._running.add(self._pool.submit(run_job, job, self.handlers, self.engine))
            started += 1
        return started

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)


def wait_for(job_id: int, timeout: float = 10.0, engine: Engine | None = None) -> dict:
    """Test/helper: block until the job is terminal."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_job(job_id, engine)
        if job and job["state"] in TERMINAL:
            return job
        time.sleep(0.02)
    raise TimeoutError(f"job {job_id} did not finish in {timeout}s")

