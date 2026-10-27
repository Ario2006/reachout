"""Job handlers, keyed by job kind. Run inside the worker process."""
from __future__ import annotations

from backend.core import db, llm
from backend.core.pipeline import run_discovery
from backend.core.jobs import Handler, JobContext


def pull_model(ctx: JobContext, payload: dict) -> dict:
    """payload: {"model": str}. Progress is per layer (0-100) with Ollama's status text."""
    model = (payload.get("model") or db.load_settings(ctx.engine).ollama_model).strip()
    if not model:
        raise ValueError("No model name given")
    ctx.progress(0, 100, f"Pulling {model}", force=True)
    for status, frac in llm.pull_model(model):
        ctx.raise_if_cancelled()
        ctx.progress(int(frac * 100) if frac is not None else 0, 100, status)
    ctx.progress(100, 100, "Done", force=True)
    return {"model": model}


HANDLERS: dict[str, Handler] = {"pull_model": pull_model, "discovery_run": run_discovery}
