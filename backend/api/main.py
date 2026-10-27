"""FastAPI app factory. Run with:  uvicorn backend.api.main:create_app --factory --port N
Environment: REACHOUT_API_TOKEN (required), REACHOUT_ALLOWED_ORIGINS (comma separated)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routers import discovery, events, health, jobs, leads, settings, setup, suppression
from backend.api.security import SecurityMiddleware
from backend.core import config, db
from backend.procutil import start_parent_watchdog

DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(token: str | None = None, allowed_origins: list[str] | None = None,
               engine=None) -> FastAPI:
    token = token or os.environ.get("REACHOUT_API_TOKEN", "")
    if allowed_origins is None:
        env = os.environ.get("REACHOUT_ALLOWED_ORIGINS", "")
        allowed_origins = [o.strip() for o in env.split(",") if o.strip()] or DEFAULT_ORIGINS

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.ensure_dirs()
        app.state.engine = engine or db.get_engine()
        db.migrate(app.state.engine)
        start_parent_watchdog()
        yield

    # No /docs or /openapi.json at runtime: smaller surface. The schema is exported by script.
    app = FastAPI(title="Reachout API", version=config.APP_VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    # Added last = outermost, so CORS preflights (which carry no token) are answered first.
    app.add_middleware(SecurityMiddleware, token=token, allowed_origins=allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        max_age=600,
    )
    for r in (health, setup, settings, suppression, jobs, events, discovery, leads):
        app.include_router(r.router)
    return app

