import os

from app import worker
from app.core import db


def test_heartbeat_job_updates_db(engine):
    assert db.worker_status(engine=engine)[0] is False
    sched = worker.build_scheduler(engine)
    # Scheduler is not started, so the job is still pending; invoke its callable directly.
    pending = sched._pending_jobs[0][0]
    pending.func()
    alive, pid, age = db.worker_status(engine=engine)
    assert alive and pid == os.getpid() and age < 5
