"""In-memory fakes for the ports. Deterministic, instant, no network. Used by every unit test and by the final
end-to-end scenario; never imported by production code."""
from __future__ import annotations

from collections.abc import Callable

from backend.core.ports import (FetchResult, ImapAuthError, IncomingMessage, MailerError, OutgoingEmail,
                                SendReceipt, SmtpAuthError, T)


class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0):  # a Friday in 2027, mid-morning UTC
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeMailer:
    """Records every send. `fail_with` maps a recipient to an exception to raise; `fail_all` raises for everyone;
    `crash_after_send` lets a test simulate the process dying right after the server accepted the message."""

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []
        self.fail_with: dict[str, Exception] = {}
        self.fail_all: Exception | None = None
        self.crash_after_send: Callable[[OutgoingEmail], None] | None = None
        self.login_error: Exception | None = None
        self.closed = False

    def send(self, email: OutgoingEmail) -> SendReceipt:
        if self.fail_all:
            raise self.fail_all
        if email.to_addr in self.fail_with:
            raise self.fail_with[email.to_addr]
        if not email.message_id:
            raise MailerError("message_id is required")
        self.sent.append(email)
        if self.crash_after_send:
            self.crash_after_send(email)
        return SendReceipt(f"250 2.0.0 OK fake-{len(self.sent)}")

    def check_login(self) -> None:
        if self.login_error:
            raise self.login_error

    def close(self) -> None:
        self.closed = True

    def to(self, addr: str) -> list[OutgoingEmail]:
        return [e for e in self.sent if e.to_addr == addr]


class FakeImap:
    """Serves raw messages (e.g. loaded from tests/fixtures/eml/*.eml) with increasing UIDs."""

    def __init__(self, uidvalidity: int = 1) -> None:
        self.uidvalidity = uidvalidity
        self.mailbox: dict[str, list[IncomingMessage]] = {}
        self.auth_error: ImapAuthError | None = None
        self.calls: list[tuple[str, int]] = []

    def deliver(self, raw: bytes | str, folder: str = "INBOX") -> int:
        box = self.mailbox.setdefault(folder, [])
        uid = max((m.uid for m in box), default=0) + 1
        box.append(IncomingMessage(uid, raw if isinstance(raw, bytes) else raw.encode()))
        return uid

    def fetch_new(self, folder: str, after_uid: int) -> FetchResult:
        if self.auth_error:
            raise self.auth_error
        self.calls.append((folder, after_uid))
        return FetchResult(self.uidvalidity, [m for m in self.mailbox.get(folder, []) if m.uid > after_uid])

    def close(self) -> None:
        pass


class FakeLlm:
    """Returns canned objects. `responses` is a list of schema instances (or callables taking (schema, system, user))
    consumed in order; the last one repeats. Raise by putting an exception instance in the list."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate_json(self, schema: type[T], system: str, user: str, *, model: str | None = None,
                      num_predict: int = 700) -> T:
        self.calls.append((system, user))
        if not self.responses:
            raise RuntimeError("FakeLlm has no response configured")
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(schema, system, user)
        return r  # type: ignore[return-value]


class FakePageCache:
    def __init__(self) -> None:
        self.pages: dict[str, dict] = {}

    def get_page(self, url: str):
        return self.pages.get(url)

    def put_page(self, url: str, final_url: str, status: int, content_type: str, html: str, tier: str,
                 etag: str | None = None, last_modified: str | None = None, ttl_s: int = 0) -> None:
        self.pages[url] = dict(url=url, final_url=final_url, status=status, content_type=content_type, html=html,
                               tier=tier, etag=etag, last_modified=last_modified, ttl_s=ttl_s)


__all__ = ["FakeClock", "FakeMailer", "FakeImap", "FakeLlm", "FakePageCache", "SmtpAuthError"]
