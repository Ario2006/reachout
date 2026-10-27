"""Background worker process: heartbeat, job runner, event-log pruning.
The send queue (M4), IMAP poller (M5) and follow-ups (M6) register here later."""
from __future__ import annotations

import logging
import os
import signal
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from backend.core import config, db, events, followups, inbox_poller, jobs, outbox, pipeline, sender
from backend.core.job_handlers import HANDLERS
from backend.core.store import CrawlStore
from backend.procutil import start_parent_watchdog

log = logging.getLogger("reachout.worker")
HEARTBEAT_EVERY_S = 5
JOB_POLL_EVERY_S = 1


def build_scheduler(engine=None, runner: jobs.JobRunner | None = None) -> BlockingScheduler:
    engine = engine or db.get_engine()
    sched = BlockingScheduler()
    sched.add_job(
        lambda: db.touch_heartbeat(os.getpid(), engine),
        "interval",
        seconds=HEARTBEAT_EVERY_S,
        next_run_time=datetime.now(),
        id="heartbeat",
        max_instances=1,
    )
    if runner is not None:
        sched.add_job(runner.tick, "interval", seconds=JOB_POLL_EVERY_S, id="jobs",
                      max_instances=1, coalesce=True)
    sched.add_job(lambda: events.prune(engine=engine), "interval", hours=1, id="prune_events")
    sched.add_job(lambda: CrawlStore(engine).prune_pages(), "interval", hours=6, id="prune_page_cache")  # pages older than 90 days
    for module in (sender, inbox_poller, followups):
        module.register(sched, engine)
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config.ensure_dirs()
    engine = db.get_engine()
    db.migrate(engine)
    recovered = jobs.recover_stale(engine)
    if recovered:
        log.warning("marked %d stale running job(s) as failed", recovered)
    pipeline.reconcile_runs(engine)
    unsure = outbox.recover_stale(engine)  # emails caught mid-send by a crash: never resent automatically
    if unsure:
        log.warning("%d email(s) were mid-send when the app stopped; they now need your confirmation", unsure)
    events.prune(engine=engine)
    runner = jobs.JobRunner(engine, HANDLERS)
    sched = build_scheduler(engine, runner)
    start_parent_watchdog()

    def _stop(*_):  # noqa: ANN002
        log.info("worker stopping")
        sched.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _stop)
    log.info("worker started pid=%s db=%s", os.getpid(), config.db_path())
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        runner.shutdown()


if __name__ == "__main__":
    main()
