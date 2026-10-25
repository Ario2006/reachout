import threading
import time

import pytest
from sqlalchemy import text

from backend.core import events, jobs, llm
from backend.core.job_handlers import HANDLERS, pull_model


def _states(engine, job_id):
    return [e["payload"]["state"] for e in events.read_since(0, 1000, engine)
            if e["type"] == "job" and e["payload"]["id"] == job_id]


def test_create_and_get(engine):
    j = jobs.create_job("demo", {"x": 1}, engine)
    assert j["state"] == "queued" and j["payload"] == {"x": 1} and j["progress"] == 0
    assert jobs.get_job(j["id"], engine)["kind"] == "demo"
    assert jobs.get_job(999, engine) is None


def test_event_payload_excludes_job_payload(engine):
    jobs.create_job("demo", {"secret_ish": "user text"}, engine)
    ev = events.read_since(0, 10, engine)[0]
    assert ev["type"] == "job" and "payload" not in ev["payload"]
    assert "secret_ish" not in str(ev)


def test_claim_is_fifo_and_exclusive(engine):
    a = jobs.create_job("k", None, engine)
    b = jobs.create_job("k", None, engine)
    assert jobs.claim_next(engine)["id"] == a["id"]
    assert jobs.claim_next(engine)["id"] == b["id"]
    assert jobs.claim_next(engine) is None


def test_run_success_records_result_and_progress(engine):
    def handler(ctx, payload):
        ctx.progress(1, 2, "half", force=True)
        ctx.progress(2, 2, "all", force=True)
        return {"ok": payload["n"]}

    j = jobs.create_job("k", {"n": 7}, engine)
    jobs.run_job(jobs.claim_next(engine), {"k": handler}, engine)
    done = jobs.get_job(j["id"], engine)
    assert done["state"] == "succeeded" and done["result"] == {"ok": 7}
    assert (done["progress"], done["total"], done["message"]) == (2, 2, "all")
    assert done["finished_at"]
    assert _states(engine, j["id"]) == ["queued", "running", "running", "running", "succeeded"]


def test_handler_exception_marks_failed_without_raising(engine):
    def boom(ctx, payload):
        raise ValueError("bad input")

    j = jobs.create_job("k", None, engine)
    jobs.run_job(jobs.claim_next(engine), {"k": boom}, engine)
    done = jobs.get_job(j["id"], engine)
    assert done["state"] == "failed" and done["error"] == "bad input"


def test_unknown_kind_fails(engine):
    j = jobs.create_job("nope", None, engine)
    jobs.run_job(jobs.claim_next(engine), {}, engine)
    assert jobs.get_job(j["id"], engine)["state"] == "failed"


def test_cancel_queued_job_is_immediate(engine):
    j = jobs.create_job("k", None, engine)
    assert jobs.request_cancel(j["id"], engine)["state"] == "cancelled"
    assert jobs.claim_next(engine) is None


def test_cancel_running_job_stops_at_checkpoint(engine):
    def loop(ctx, payload):
        for i in range(1000):
            ctx.raise_if_cancelled()
            time.sleep(0.01)

    j = jobs.create_job("k", None, engine)
    claimed = jobs.claim_next(engine)
    t = threading.Thread(target=jobs.run_job, args=(claimed, {"k": loop}, engine))
    t.start()
    time.sleep(0.05)
    assert jobs.request_cancel(j["id"], engine)["cancel_requested"] is True
    t.join(5)
    assert jobs.get_job(j["id"], engine)["state"] == "cancelled"


def test_cancel_terminal_job_is_noop(engine):
    j = jobs.create_job("k", None, engine)
    jobs.run_job(jobs.claim_next(engine), {"k": lambda c, p: None}, engine)
    assert jobs.request_cancel(j["id"], engine)["state"] == "succeeded"


def test_progress_is_throttled(engine):
    j = jobs.create_job("k", None, engine)
    ctx = jobs.JobContext(j["id"], engine, min_interval_s=60)
    for i in range(50):
        ctx.progress(i)
    n = len([e for e in events.read_since(0, 1000, engine) if e["payload"]["id"] == j["id"]])
    assert n == 2  # created + the first progress update only


def test_recover_stale_running_jobs(engine):
    j = jobs.create_job("k", None, engine)
    jobs.claim_next(engine)
    assert jobs.recover_stale(engine) == 1
    got = jobs.get_job(j["id"], engine)
    assert got["state"] == "failed" and "restarted" in got["error"]


def test_runner_executes_queued_jobs(engine):
    j = jobs.create_job("k", {"v": 3}, engine)
    runner = jobs.JobRunner(engine, {"k": lambda c, p: {"v": p["v"] * 2}})
    assert runner.tick() == 1
    assert jobs.wait_for(j["id"], 5, engine)["result"] == {"v": 6}
    assert runner.tick() == 0
    runner.shutdown(wait=True)


def test_runner_respects_max_concurrent(engine):
    gate = threading.Event()
    runner = jobs.JobRunner(engine, {"k": lambda c, p: gate.wait(5)}, max_concurrent=2)
    ids = [jobs.create_job("k", None, engine)["id"] for _ in range(3)]
    assert runner.tick() == 2
    assert jobs.get_job(ids[2], engine)["state"] == "queued"
    gate.set()
    for i in ids[:2]:
        jobs.wait_for(i, 5, engine)
    assert runner.tick() == 1
    jobs.wait_for(ids[2], 5, engine)
    runner.shutdown(wait=True)


# ---- events

def test_events_ordering_and_since(engine):
    ids = [events.emit("t", {"i": i}, engine) for i in range(5)]
    assert [e["payload"]["i"] for e in events.read_since(ids[1], 100, engine)] == [2, 3, 4]
    assert len(events.read_since(0, 2, engine)) == 2
    assert events.latest_id(engine) == ids[-1]


def test_events_prune_keeps_newest(engine):
    for i in range(10):
        events.emit("t", {"i": i}, engine)
    assert events.prune(keep=3, engine=engine) == 7
    assert [e["payload"]["i"] for e in events.read_since(0, 100, engine)] == [7, 8, 9]


# ---- pull_model handler (Ollama mocked)

def test_pull_model_handler_reports_progress(engine, monkeypatch):
    monkeypatch.setattr(llm, "pull_model",
                        lambda name: iter([("pulling manifest", None), ("pulling abc", 0.5),
                                           ("success", 1.0)]))
    j = jobs.create_job("pull_model", {"model": "m:1b"}, engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    done = jobs.get_job(j["id"], engine)
    assert done["state"] == "succeeded" and done["result"] == {"model": "m:1b"}
    assert done["progress"] == 100


def test_pull_model_handler_cancellable(engine, monkeypatch):
    def slow(name):
        for i in range(1000):
            time.sleep(0.01)
            yield ("pulling", i / 1000)

    monkeypatch.setattr(llm, "pull_model", slow)
    j = jobs.create_job("pull_model", {"model": "m"}, engine)
    t = threading.Thread(target=jobs.run_job, args=(jobs.claim_next(engine), HANDLERS, engine))
    t.start()
    time.sleep(0.1)
    jobs.request_cancel(j["id"], engine)
    t.join(5)
    assert jobs.get_job(j["id"], engine)["state"] == "cancelled"


def test_pull_model_error_marks_failed(engine, monkeypatch):
    def bad(name):
        raise RuntimeError("manifest does not exist")
        yield  # pragma: no cover

    monkeypatch.setattr(llm, "pull_model", bad)
    j = jobs.create_job("pull_model", {"model": "nope"}, engine)
    jobs.run_job(jobs.claim_next(engine), HANDLERS, engine)
    got = jobs.get_job(j["id"], engine)
    assert got["state"] == "failed" and "manifest" in got["error"]
