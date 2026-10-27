"""Draft generation (milestone 3, owner B). Contract stub until B replaces it."""
from __future__ import annotations

from backend.core.jobs import JobContext


def run_generate_drafts(ctx: JobContext, payload: dict) -> dict:
    """Job `drafts_generate`. payload = GenerateDraftsIn. Result: {"generated": n, "needs_review": n, "skipped": n}."""
    raise NotImplementedError("drafts_generate: milestone 3")  # pragma: no cover - contract stub
