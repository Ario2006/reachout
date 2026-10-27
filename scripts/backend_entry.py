"""Entry point of the frozen (PyInstaller) backend: one executable, two roles.

    reachout-backend api [--host H] [--port N] [--log-level L]   # == uvicorn backend.api.main:create_app --factory
    reachout-backend worker                                       # == python -m backend.worker

Only used for the packaged app (scripts/build-mac.sh); running from source uses .venv and uvicorn directly.
"""
from __future__ import annotations

import argparse
import multiprocessing
import sys


def selfcheck() -> int:
    """Exercise the optional/lazy dependencies that PyInstaller can miss (no network, no Ollama, no Keychain writes)."""
    import importlib
    import pkgutil

    import backend

    failed: list[str] = []
    for m in pkgutil.walk_packages(backend.__path__, "backend."):  # every backend module imports cleanly
        if m.name.endswith(("export_openapi", "cli")):
            continue
        try:
            importlib.import_module(m.name)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{m.name}: {e!r}")

    def check(name: str, fn) -> None:  # noqa: ANN001
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed.append(f"{name}: {e!r}")

    def _tld() -> None:
        from tld import get_fld

        assert get_fld("https://www.example.co.uk/x") == "example.co.uk"

    def _extract() -> None:
        import trafilatura
        from selectolax.lexbor import LexborHTMLParser as HTMLParser

        html = "<html><head><title>T</title></head><body><article><p>" + "Hello world. " * 40 + "</p></article></body></html>"
        assert trafilatura.extract(html, include_comments=False) is not None
        assert HTMLParser(html).css_first("title").text() == "T"

    def _keyring() -> None:
        import keyring

        assert "macOS" in type(keyring.get_keyring()).__name__ or "Keyring" in type(keyring.get_keyring()).__name__

    def _misc() -> None:
        import dns.resolver  # noqa: F401
        import email_validator
        import ddgs  # noqa: F401
        import psutil
        import zoneinfo

        email_validator.validate_email("a@example.com", check_deliverability=False)
        psutil.Process().memory_info()
        zoneinfo.ZoneInfo("Asia/Kolkata")

    def _apscheduler() -> None:
        from backend.worker import build_scheduler

        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        from backend.core import db

        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        db.migrate(engine)  # also proves the bundled SQL migrations are readable
        build_scheduler(engine=engine)  # builds triggers/executors via entry points

    def _migrations() -> None:
        from backend.core import db

        assert sorted(db.MIGRATIONS_DIR.glob("*.sql")), f"no migrations in {db.MIGRATIONS_DIR}"

    for name, fn in [("tld", _tld), ("extract", _extract), ("keyring", _keyring), ("misc", _misc),
                     ("apscheduler", _apscheduler), ("migrations", _migrations)]:
        check(name, fn)
    if failed:
        print("selfcheck FAILED:\n  " + "\n  ".join(failed))
        return 1
    print("selfcheck ok")
    return 0


def main(argv: list[str]) -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog="reachout-backend")
    parser.add_argument("role", choices=["api", "worker", "selfcheck"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    if args.role == "selfcheck":
        return selfcheck()

    if args.role == "worker":
        from backend.worker import main as worker_main

        worker_main()
        return 0

    import uvicorn

    from backend.api.main import create_app

    uvicorn.run(create_app, factory=True, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
