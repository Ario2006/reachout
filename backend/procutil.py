"""Process helpers shared by the API and the worker."""
from __future__ import annotations

import logging
import os
import signal
import threading
import time

log = logging.getLogger("reachout.proc")


def start_parent_watchdog(poll_s: float = 2.0) -> bool:
    """If REACHOUT_WATCH_PARENT=1 (set by the Electron supervisor), exit when the parent dies.
    Stops orphaned backend processes if Electron is killed without cleaning up."""
    if os.environ.get("REACHOUT_WATCH_PARENT") != "1":
        return False
    parent = os.getppid()

    def _watch() -> None:
        while True:
            time.sleep(poll_s)
            if os.getppid() != parent:
                log.warning("parent process %s is gone; shutting down", parent)
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=_watch, name="parent-watchdog", daemon=True).start()
    return True
