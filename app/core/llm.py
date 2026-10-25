"""Thin Ollama wrapper. Milestone 1: reachability, model listing, model pull.
Structured-output calls arrive with the planner in Milestone 2."""
from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import httpx
import ollama


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
