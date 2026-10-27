"""Thin Ollama wrapper: reachability, model listing, model pull, structured (JSON-schema) calls."""
from __future__ import annotations

import logging
import os
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar

import httpx
import ollama
from pydantic import BaseModel, ValidationError

log = logging.getLogger("reachout.llm")
T = TypeVar("T", bound=BaseModel)

# One model call at a time in this process (8 GB machine: never two generations at once).
_CALL_LOCK = threading.Lock()
# ~3,000 tokens of page text; ~4 characters per token.
MAX_PROMPT_CHARS = 12_000


def host() -> str:
    h = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
    if not h.startswith(("http://", "https://")):
        h = "http://" + h
    return h.rstrip("/")


def is_installed() -> bool:
    return bool(shutil.which("ollama")) or Path("/opt/homebrew/bin/ollama").exists()


def is_running(timeout: float = 2.0) -> bool:
    try:
        return httpx.get(host() + "/", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def list_models() -> list[str]:
    resp = ollama.Client(host=host()).list()
    return [m.model for m in resp.models if m.model]


def has_model(name: str, installed: list[str] | None = None) -> bool:
    installed = list_models() if installed is None else installed
    wanted = {name, name if ":" in name else f"{name}:latest"}
    return any(m in wanted for m in installed)


def pull_model(name: str) -> Iterator[tuple[str, float | None]]:
    """Yield (status, fraction 0..1 or None) while pulling."""
    for p in ollama.Client(host=host()).pull(name, stream=True):
        frac = (p.completed / p.total) if p.total and p.completed is not None else None
        yield (p.status or "", frac)


class LlmError(Exception):
    """The model was unreachable or returned something unusable after retries."""


def truncate(text: str, limit: int = MAX_PROMPT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit]


def generate_json(
    schema: type[T],
    system: str,
    user: str,
    *,
    model: str,
    num_ctx: int = 4096,
    keep_alive: str = "5m",
    retries: int = 1,
    timeout_s: float = 120.0,
    max_chars: int = MAX_PROMPT_CHARS,
    num_predict: int = 700,
) -> T:
    """Ask the model for JSON matching `schema` (structured outputs) and validate it.
    Retries once on invalid output. Raises LlmError on failure."""
    client = ollama.Client(host=host(), timeout=timeout_s)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _CALL_LOCK:
                resp = client.chat(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": truncate(user, max_chars)}],
                    format=schema.model_json_schema(),
                    options={"num_ctx": num_ctx, "temperature": 0.1, "num_predict": num_predict},
                    keep_alive=keep_alive,
                )
            return schema.model_validate_json(resp.message.content or "")
        except ValidationError as e:
            last = e
            log.warning("model returned invalid JSON (attempt %d): %s", attempt + 1, str(e)[:200])
        except Exception as e:  # noqa: BLE001 - connection errors, model missing, timeouts
            raise LlmError(str(e) or type(e).__name__) from e
    raise LlmError(f"Invalid model output: {last}")
