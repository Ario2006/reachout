"""Email validation: syntax, MX records, confidence labels, dedupe."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

from backend.core import domains
from backend.core.models import EmailConfidence

log = logging.getLogger("reachout.validator")
MX_TTL_S = 6 * 3600


def normalize_syntax(email: str) -> str | None:
    """Lowercased, normalized address if the syntax is valid (no network); else None."""
    try:
        return validate_email(email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


class MxChecker:
    """MX lookups with a per-domain cache. `has_mx` is True/False, or None if DNS stayed unreachable."""

    def __init__(self, resolver: dns.resolver.Resolver | None = None, lookup: Callable[[str], bool | None] | None = None):
        self._resolver = resolver
        self._lookup = lookup
        self._cache: dict[str, tuple[float, bool | None]] = {}
        self._lock = threading.Lock()

    def _resolve(self, domain: str) -> bool | None:
        if self._lookup is not None:
            return self._lookup(domain)
        res = self._resolver or dns.resolver.Resolver()
        res.lifetime, res.timeout = 5.0, 3.0
        for attempt in range(2):
            try:
                answers = res.resolve(domain, "MX")
                # A single "." exchange is a null MX: the domain explicitly accepts no mail.
                return any(str(a.exchange).rstrip(".") for a in answers)
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
                return False
            except (dns.exception.Timeout, dns.resolver.LifetimeTimeout, OSError):
                if attempt == 0:
                    time.sleep(0.5)
        return None

    def has_mx(self, domain: str) -> bool | None:
        domain = domain.strip().lower().rstrip(".")
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(domain)
            if cached and now - cached[0] < MX_TTL_S and cached[1] is not None:
                return cached[1]
        result = self._resolve(domain)
        with self._lock:
            self._cache[domain] = (now, result)
        return result

    def check_email(self, email: str) -> bool | None:
        return self.has_mx(domains.split_email(email)[1])


def classify_confidence(email: str) -> EmailConfidence:
    """For addresses of unknown provenance (CSV imports): role address -> generic, else personal."""
    local = domains.split_email(email)[0]
    if domains.is_generic_local(local) or not domains.namelike_local(local):
        return EmailConfidence.published_generic
    return EmailConfidence.published_personal
