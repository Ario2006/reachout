"""Single-consumer, batching queue for the local model.

The 8 GB machine never runs two generations at once, and a call's cost is dominated by its output, so
several short page snippets share one call. The crawl never waits on this queue: callers `submit` and
await a future, while the HTTP pipeline keeps running (the queue is bounded so memory stays flat)."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field

from backend.core import llm
from backend.core.extractor import Person, _TITLE_HINT_RE, _valid_name
from backend.core.models import AppSettings

log = logging.getLogger("reachout.llmqueue")

BATCH_CHARS = 12_000  # about 3,000 tokens of page text per call
SNIPPET_CHARS = 6_000  # one page never takes more than this much of a batch
MAX_WAIT_S = 0.4  # how long to wait for more snippets before running a partial batch
DOWN_AFTER = 2  # consecutive failed calls before we stop trying for the rest of the run


class PagePeople(BaseModel):
    page: int
    people: list[Person] = Field(default_factory=list, max_length=12)


class BatchPeople(BaseModel):
    pages: list[PagePeople] = Field(default_factory=list)


SYSTEM = (
    "You read several web page excerpts, each marked '### PAGE n'. For each page list the real people "
    "(founders, executives, partners, team members) it names, with their job title exactly as written (empty "
    "string if none). Do NOT include company names, job titles, job postings, products or places, and never "
    "invent names. Return JSON: {\"pages\": [{\"page\": n, \"people\": [{\"name\": ..., \"role\": ...}]}]} with an "
    "entry for every page, at most 12 people each."
)


def snippet_for_llm(text: str, limit: int = SNIPPET_CHARS) -> str:
    """The most useful `limit` characters of a page: if it must be cut, keep the stretch around the first
    job titles (team lists) rather than the top of the page, which is usually navigation."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    m = _TITLE_HINT_RE.search(text)
    start = max(0, (m.start() if m else 0) - 400)
    return text[start:start + limit]


@dataclass
class _Item:
    domain: str
    url: str
    snippet: str
    fut: asyncio.Future


Caller = Callable[[str, int], Awaitable[BatchPeople]]


class PeopleBatcher:
    def __init__(self, settings: AppSettings | None = None, *, call: Caller | None = None, batch_chars: int = BATCH_CHARS,
                 max_wait_s: float = MAX_WAIT_S, queue_size: int = 64):
        self.settings, self.batch_chars, self.max_wait = settings, batch_chars, max_wait_s
        self._call = call or self._default_call
        self._q: asyncio.Queue[_Item] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task | None = None
        self._carry: _Item | None = None  # a snippet that did not fit the last batch
        self.calls = self.items = self.failed = self._consecutive = 0
        self.seconds = 0.0
        self.down = False
        self.active = self.max_active = 0  # for tests: this must never exceed 1
        self._pool: ThreadPoolExecutor | None = None  # owned, so close() never waits on a slow generation

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="people-batcher")

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._carry and not self._carry.fut.done():
            self._carry.fut.cancel()
        while not self._q.empty():  # nobody is left to answer these
            it = self._q.get_nowait()
            if not it.fut.done():
                it.fut.cancel()

    async def submit(self, domain: str, url: str, text: str) -> list[Person]:
        """Queue a page excerpt and wait for its people. Raises llm.LlmError if the model is unavailable."""
        if self.down:
            raise llm.LlmError("The language model stopped responding")
        self.start()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._q.put(_Item(domain, url, snippet_for_llm(text), fut))  # blocks when full: back-pressure
        return await fut

    async def _collect(self) -> list[_Item]:
        first, self._carry = (self._carry, None) if self._carry else (await self._q.get(), None)
        batch, used = [first], len(first.snippet)
        deadline = time.monotonic() + self.max_wait
        while used < self.batch_chars:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                nxt = await asyncio.wait_for(self._q.get(), remaining)
            except asyncio.TimeoutError:
                break
            if used + len(nxt.snippet) > self.batch_chars:
                # Does not fit: run what we have, and keep this one for the next call.
                self._carry = nxt
                break
            batch.append(nxt)
            used += len(nxt.snippet)
        return batch

    async def _run(self) -> None:
        while True:
            batch = await self._collect()
            body = "\n\n".join(f"### PAGE {i} (site: {it.domain})\n{it.snippet}" for i, it in enumerate(batch, 1))
            t0 = time.monotonic()
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                out = await self._call(body, len(batch))
                self._consecutive = 0
                self._deliver(batch, out)
            except llm.LlmError as e:
                self.failed += 1
                self._consecutive += 1
                if self._consecutive >= DOWN_AFTER:
                    self.down = True
                for it in batch:
                    if not it.fut.done():
                        it.fut.set_exception(e)
                if self.down:
                    self._fail_queued(e)
            finally:
                self.active -= 1
                self.calls += 1
                self.items += len(batch)
                self.seconds += time.monotonic() - t0

    def _fail_queued(self, e: Exception) -> None:
        while not self._q.empty():
            it = self._q.get_nowait()
            if not it.fut.done():
                it.fut.set_exception(e)

    @staticmethod
    def _deliver(batch: list[_Item], out: BatchPeople) -> None:
        by_page = {p.page: p.people for p in out.pages}
        for i, it in enumerate(batch, 1):
            low = it.snippet.lower()
            people, seen = [], set()
            for p in by_page.get(i, []):
                name = _valid_name(p.name, low)  # the name must literally appear in THIS page's text
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    people.append(Person(name=name, role=re.sub(r"\s+", " ", p.role).strip()[:80]))
            if not it.fut.done():
                it.fut.set_result(people)

    async def _default_call(self, body: str, n: int) -> BatchPeople:
        s = self.settings or AppSettings()
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-batch")
        return await asyncio.get_running_loop().run_in_executor(
            self._pool, lambda: llm.generate_json(
                BatchPeople, SYSTEM, body, model=s.ollama_model, num_ctx=s.num_ctx, keep_alive=s.keep_alive,
                max_chars=self.batch_chars + 2000, num_predict=min(1400, 300 + 250 * n)))

    def snapshot(self) -> dict:
        return {"calls": self.calls, "pages": self.items, "failed": self.failed, "seconds": round(self.seconds, 1),
                "pages_per_call": round(self.items / self.calls, 2) if self.calls else 0.0, "down": self.down}
