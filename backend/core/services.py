"""One place that hands out the ports. Production code calls `get_services()`; tests call `use(fakes())`.

The real mailer and IMAP client are imported lazily from the modules the owning milestones create, so this file
never changes when they land."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from backend.core import db, llm
from backend.core.fakes import FakeClock, FakeImap, FakeLlm, FakeMailer
from backend.core.ports import Clock, ImapClient, LlmClient, Mailer, T


class SystemClock:
    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class OllamaClient:
    def __init__(self, engine=None):
        self.engine = engine

    def generate_json(self, schema: type[T], system: str, user: str, *, model: str | None = None,
                      num_predict: int = 700) -> T:
        s = db.load_settings(self.engine)
        return llm.generate_json(schema, system, user, model=model or s.ollama_model, num_ctx=s.num_ctx,
                                 keep_alive=s.keep_alive, num_predict=num_predict)


@dataclass
class Services:
    mailer: Callable[[], Mailer]  # a factory: credentials are read from the Keychain per use, never held
    imap: Callable[[], ImapClient]
    llm: LlmClient
    clock: Clock


def _real_mailer() -> Mailer:
    from backend.core.mailer import SmtpMailer  # milestone 4

    return SmtpMailer.from_settings()


def _real_imap() -> ImapClient:
    from backend.core.tracker import GmailImap  # milestone 5

    return GmailImap.from_settings()


def production() -> Services:
    return Services(mailer=_real_mailer, imap=_real_imap, llm=OllamaClient(), clock=SystemClock())


@dataclass
class Fakes(Services):
    fake_mailer: FakeMailer | None = None
    fake_imap: FakeImap | None = None
    fake_llm: FakeLlm | None = None
    fake_clock: FakeClock | None = None


def fakes(*llm_responses: object) -> Fakes:
    m, i, l, c = FakeMailer(), FakeImap(), FakeLlm(*llm_responses), FakeClock()
    return Fakes(mailer=lambda: m, imap=lambda: i, llm=l, clock=c, fake_mailer=m, fake_imap=i, fake_llm=l, fake_clock=c)


_current: Services | None = None


def get_services() -> Services:
    global _current
    if _current is None:
        _current = production()
    return _current


def use(services: Services | None) -> None:
    """Install `services` (or None to go back to production). Tests only."""
    global _current
    _current = services
