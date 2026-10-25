"""GET /events: Server-Sent Events tailing the event_log table."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import anyio
from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from backend.api.deps import EngineDep
from backend.core import events

router = APIRouter(tags=["events"])


def format_sse(ev: dict) -> str:
    data = json.dumps({"payload": ev["payload"], "created_at": ev["created_at"]})
    return f"id: {ev['id']}\nevent: {ev['type']}\ndata: {data}\n\n"


async def event_stream(
    engine,
    last_id: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    poll_s: float = 0.4,
    heartbeat_s: float = 15.0,
) -> AsyncIterator[str]:
    yield "retry: 2000\n: connected\n\n"
    last_beat = time.monotonic()
    while not await is_disconnected():
        batch = await anyio.to_thread.run_sync(events.read_since, last_id, 200, engine)
        for ev in batch:
            last_id = ev["id"]
            yield format_sse(ev)
        if batch:
            continue  # drain backlog before sleeping
        if time.monotonic() - last_beat >= heartbeat_s:
            last_beat = time.monotonic()
            yield ": keep-alive\n\n"
        await asyncio.sleep(poll_s)


@router.get("/events")
async def stream_events(request: Request, engine: EngineDep,
                        last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    """New events only by default; send Last-Event-ID to resume after a reconnect."""
    try:
        start = int(last_event_id) if last_event_id else None
    except ValueError:
        start = None
    if start is None:
        start = await anyio.to_thread.run_sync(events.latest_id, engine)
    return StreamingResponse(
        event_stream(engine, start, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
