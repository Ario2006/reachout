import dns.exception
import dns.resolver
import pytest

from backend.core import validator as v
from backend.core.models import EmailConfidence


@pytest.mark.parametrize("raw,expected", [
    ("Priya@Acme.AI", "priya@acme.ai"), ("  a@b.co  ", "a@b.co"), ("a+tag@b.com", "a+tag@b.com"),
    ("no-at-sign", None), ("a@", None), ("@b.com", None), ("a b@c.com", None), ("a@b", None), ("", None),
    ("a@@b.com", None), ("a@-bad.com", None),
])
def test_syntax(raw, expected):
    assert v.normalize_syntax(raw) == expected


def test_mx_cache_one_lookup_per_domain():
    calls = []
    mx = v.MxChecker(lookup=lambda d: calls.append(d) or True)
    assert mx.check_email("a@acme.ai") and mx.check_email("b@ACME.ai") and mx.has_mx("acme.ai.")
    assert calls == ["acme.ai"]


def test_unknown_dns_result_is_not_cached():
    results = iter([None, True])
    mx = v.MxChecker(lookup=lambda d: next(results))
    assert mx.has_mx("a.com") is None
    assert mx.has_mx("a.com") is True  # retried because the first outcome was inconclusive


class Ans:
    def __init__(self, ex): self.exchange = ex


class FakeResolver:
    def __init__(self, outcome): self.outcome, self.lifetime, self.timeout, self.n = outcome, None, None, 0

    def resolve(self, domain, rtype):
        self.n += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize("outcome,expected", [
    ([Ans("mx1.acme.ai.")], True),
    ([Ans(".")], False),  # null MX: the domain accepts no mail
    (dns.resolver.NXDOMAIN(), False),
    (dns.resolver.NoAnswer(), False),
    (dns.resolver.NoNameservers(), False),
])
def test_real_resolver_paths(outcome, expected):
    assert v.MxChecker(resolver=FakeResolver(outcome)).has_mx("acme.ai") is expected


def test_timeout_retries_once_then_reports_unknown(monkeypatch):
    monkeypatch.setattr(v.time, "sleep", lambda s: None)
    r = FakeResolver(dns.exception.Timeout())
    assert v.MxChecker(resolver=r).has_mx("slow.com") is None and r.n == 2


@pytest.mark.parametrize("email,conf", [
    ("info@acme.com", EmailConfidence.published_generic), ("careers@acme.com", EmailConfidence.published_generic),
    ("priya@acme.com", EmailConfidence.published_personal), ("john.smith@acme.com", EmailConfidence.published_personal),
    ("x1@acme.com", EmailConfidence.published_generic), ("marketing@acme.com", EmailConfidence.published_generic),
])
def test_classify_confidence(email, conf):
    assert v.classify_confidence(email) == conf
