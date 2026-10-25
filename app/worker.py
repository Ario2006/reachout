"""Background worker process. Milestone 1: heartbeat only.
Crawl jobs (M2), the send queue (M4), the IMAP poller (M5) and follow-ups (M6) register here."""
from __future__ import annotations

import logging
import os
import signal
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from app.core import config, db

log = logging.getLogger("reachout.worker")
HEARTBEAT_EVERY_S = 5


def build_scheduler(engine=None) -> BlockingScheduler:
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
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config.ensure_dirs()
    engine = db.get_engine()
    db.migrate(engine)
    sched = build_scheduler(engine)

    def _stop(*_):  # noqa: ANN002
        log.info("worker stopping")
        sched.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _stop)
    log.info("worker started pid=%s db=%s", os.getpid(), config.db_path())
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
