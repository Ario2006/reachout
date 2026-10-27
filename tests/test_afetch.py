import asyncio

import httpx
import pytest

from backend.core import afetch
from backend.core.afetch import AsyncFetcher, BrowserTier
from backend.core.store import CrawlStore

HTML = "<html><head><title>Acme</title></head><body>" + "<p>We build things. Contact us at hello@acme.com for more.</p>" * 20 + "</body></html>"


class FakeTime:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.t

    async def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
        await asyncio.sleep(0)


class StoreClock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t


class Site:
    """A scripted fake web: routes map 'host/path' to a response or a callable(request) -> response."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests: list[httpx.Request] = []
        self.times: list[tuple[str, float]] = []
        self.ft: FakeTime | None = None
        self.active = self.max_active = 0
        self.delay = 0.0

    def count(self, suffix):
        return sum(1 for r in self.requests if r.url.path.endswith(suffix) if suffix) if suffix else len(self.requests)

    async def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.ft:
            self.times.append((req.url.host, self.ft.t))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            r = self.routes.get(f"{req.url.host}{req.url.path}")
            if r is None:
                r = httpx.Response(404, text="nope", headers={"content-type": "text/html"})
            if callable(r):
                r = r(req)
            if isinstance(r, Exception):
                raise r
            if isinstance(r, str):
                r = httpx.Response(200, text=r, headers={"content-type": "text/html; charset=utf-8"})
            return r
        finally:
            self.active -= 1


def make(engine, site, **kw):
    ft = FakeTime()
    site.ft = ft
    sc = StoreClock()
    store = CrawlStore(engine, sc)
    client = httpx.AsyncClient(transport=httpx.MockTransport(site), follow_redirects=True)
    opts = dict(client=client, check_public=False, sleep=ft.sleep, clock=ft.clock, rand=lambda a, b: a, delay_s=(1.0, 2.0))
    opts.update(kw)
    return AsyncFetcher(store, **opts), ft, store, sc


def run(coro):
    return asyncio.run(coro)


def html_response(body=HTML, **headers):
    return httpx.Response(200, text=body, headers={"content-type": "text/html", **headers})


# ---- tiers and cache ------------------------------------------------------------------------

def test_miss_then_hit_the_second_fetch_never_touches_the_network(engine):
    site = Site({"a.com/": HTML})
    f, ft, store, sc = make(engine, site)

    async def go():
        a = await f.fetch("https://a.com/")
        n = len(site.requests)
        b = await f.fetch("https://a.com/")
        return a, b, n

    a, b, n = run(go())
    assert (a.ok, a.tier, a.cache) == (True, "http", "miss")
    assert (b.ok, b.tier, b.cache, b.origin_tier) == (True, "cache", "hit", "http") and b.html == a.html
    assert len(site.requests) == n  # robots.txt + the page, nothing more
    s = f.snapshot()
    assert s["cache_hit_rate"] == 0.5 and s["tiers"] == {"cache": 1, "http": 1, "browser": 0}


def test_a_second_fetcher_on_the_same_store_is_served_from_cache_including_robots(engine):
    site = Site({"a.com/": HTML, "a.com/robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /")})
    f1, _, store, sc = make(engine, site)
    run(f1.fetch("https://a.com/"))
    first = len(site.requests)
    client = httpx.AsyncClient(transport=httpx.MockTransport(site))
    ft = FakeTime()
    f2 = AsyncFetcher(store, client=client, check_public=False, sleep=ft.sleep, clock=ft.clock)
    r = run(f2.fetch("https://a.com/"))
    assert r.cache == "hit" and len(site.requests) == first


def test_stale_page_is_revalidated_with_a_conditional_request(engine):
    n = {"calls": 0}

    def page(req):
        n["calls"] += 1
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return html_response(etag='"v1"', **{"last-modified": "Mon, 01 Jan 2026 00:00:00 GMT"})

    site = Site({"a.com/": page})
    f, ft, store, sc = make(engine, site, ttl_s=100)

    async def go():
        await f.fetch("https://a.com/")
        sc.t += 200  # stale
        return await f.fetch("https://a.com/")

    r = run(go())
    assert (r.cache, r.tier, r.ok) == ("revalidated", "cache", True)
    page_reqs = [q for q in site.requests if q.url.path == "/"]
    assert page_reqs[1].headers["if-none-match"] == '"v1"' and page_reqs[1].headers["if-modified-since"].startswith("Mon, 01 Jan 2026")
    assert store.get_page("https://a.com/").fresh(sc())  # the 304 restarted the TTL


def test_stale_page_without_validators_is_refetched_and_updated(engine):
    bodies = iter([HTML, HTML.replace("hello@acme.com", "new@acme.com")])
    site = Site({"a.com/": lambda req: html_response(next(bodies))})
    f, ft, store, sc = make(engine, site, ttl_s=100)

    async def go():
        await f.fetch("https://a.com/")
        sc.t += 101
        return await f.fetch("https://a.com/")

    r = run(go())
    assert r.cache == "miss" and "new@acme.com" in r.html


def test_definite_failures_are_cached_so_blind_guesses_are_never_repeated(engine):
    site = Site({})
    f, ft, store, sc = make(engine, site)

    async def go():
        a = await f.fetch("https://a.com/team")
        n = len(site.requests)
        b = await f.fetch("https://a.com/team")
        return a, b, n

    a, b, n = run(go())
    assert a.reason == "http_404" and a.status == 404 and b.reason == "http_404" and b.tier == "cache"
    assert len(site.requests) == n


def test_non_html_is_rejected_and_remembered(engine):
    site = Site({"a.com/doc": httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})})
    f, ft, store, sc = make(engine, site)

    async def go():
        a = await f.fetch("https://a.com/doc")
        n = len(site.requests)
        b = await f.fetch("https://a.com/doc")
        return a, b, n

    a, b, n = run(go())
    assert a.reason == "non_html" and b.reason == "non_html" and len(site.requests) == n


def test_js_shell_goes_to_the_browser_tier_and_is_cached_as_browser(engine):
    shell = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    rendered = "<html><body><h1>Team</h1><p>" + "Priya Sharma, CEO. priya@acme.com " * 20 + "</p></body></html>"
    site = Site({"a.com/team": shell})

    class FakeBrowser:
        def __init__(self, out): self.calls, self.out = [], out
        async def render(self, url):
            self.calls.append(url)
            return self.out
        async def aclose(self): pass

    b = FakeBrowser(rendered)
    f, ft, store, sc = make(engine, site, browser=b)

    async def go():
        a = await f.fetch("https://a.com/team")
        c = await f.fetch("https://a.com/team")
        return a, c

    a, c = run(go())
    assert (a.tier, a.ok) == ("browser", True) and "priya@acme.com" in a.html and b.calls == ["https://a.com/team"]
    assert c.tier == "cache" and c.origin_tier == "browser" and len(b.calls) == 1
    assert f.snapshot()["tiers"]["browser"] == 1


@pytest.mark.parametrize("browser_out", [None, "<html><body></body></html>"])
def test_browser_tier_failing_or_no_better_keeps_the_http_page(engine, browser_out):
    shell = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'

    class B:
        async def render(self, url): return browser_out
        async def aclose(self): pass

    f, *_ = make(engine, Site({"a.com/": shell}), browser=B())
    r = run(f.fetch("https://a.com/"))
    assert r.tier == "http" and r.ok


def test_pages_with_real_content_never_use_the_browser(engine):
    class B:
        calls = 0
        async def render(self, url):
            B.calls += 1
        async def aclose(self): pass

    f, *_ = make(engine, Site({"a.com/": HTML}), browser=B())
    run(f.fetch("https://a.com/"))
    assert B.calls == 0


def test_shell_detection():
    assert afetch.looks_like_js_shell('<html><body><div id="__next"></div><script src="x.js"></script></body></html>')
    assert not afetch.looks_like_js_shell(HTML)
    assert not afetch.looks_like_js_shell("<html><body>tiny</body></html>")  # no scripts: just an empty page
    assert not afetch.looks_like_js_shell("<html><body><script>1</script>Just a moment...</body></html>")  # a challenge, not a shell


# ---- robots ---------------------------------------------------------------------------------

def test_robots_disallow_blocks_the_fetch_before_any_page_request(engine):
    site = Site({"a.com/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /private"), "a.com/private/x": HTML, "a.com/ok": HTML})
    f, *_ = make(engine, site)

    async def go():
        return await f.fetch("https://a.com/private/x"), await f.fetch("https://a.com/ok")

    blocked, ok = run(go())
    assert blocked.reason == "robots" and ok.ok
    assert not any(r.url.path == "/private/x" for r in site.requests)


@pytest.mark.parametrize("resp,fetchable", [
    (httpx.Response(404), True), (httpx.Response(403), True), (httpx.Response(410), True),
    (httpx.Response(500), False), (httpx.Response(503), False), (httpx.ConnectError("down"), False),
])
def test_robots_status_handling_follows_rfc_9309(engine, resp, fetchable):
    f, *_ = make(engine, Site({"a.com/robots.txt": resp, "a.com/": HTML}))
    assert run(f.fetch("https://a.com/")).ok is fetchable


def test_unreachable_robots_is_rechecked_soon_not_after_a_day(engine):
    calls = {"n": 0}

    def robots(req):
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] == 1 else httpx.Response(200, text="User-agent: *\nAllow: /")

    site = Site({"a.com/robots.txt": robots, "a.com/": HTML})
    f1, ft, store, sc = make(engine, site)
    assert run(f1.fetch("https://a.com/")).reason == "robots"
    sc.t += 601  # past the 10-minute failure TTL, far short of 24 hours
    f2 = AsyncFetcher(store, client=httpx.AsyncClient(transport=httpx.MockTransport(site)), check_public=False, sleep=ft.sleep, clock=ft.clock)
    assert run(f2.fetch("https://a.com/")).ok


def test_robots_for_a_good_site_is_fetched_once_per_day_and_sitemaps_are_read(engine):
    txt = "User-agent: *\nCrawl-delay: 4\nSitemap: https://a.com/sitemap.xml\nDisallow: /x"
    site = Site({"a.com/robots.txt": httpx.Response(200, text=txt), "a.com/": HTML, "a.com/a": HTML, "a.com/b": HTML})
    f, ft, store, sc = make(engine, site)

    async def go():
        for u in ("/", "/a", "/b"):
            await f.fetch("https://a.com" + u)
        return await f.sitemaps_for("a.com")

    assert run(go()) == ["https://a.com/sitemap.xml"]
    assert sum(1 for r in site.requests if r.url.path == "/robots.txt") == 1
    assert store.get_domain("a.com").crawl_delay == 4.0


def test_crawl_delay_raises_the_gap_between_requests_to_one_host(engine):
    site = Site({"a.com/robots.txt": httpx.Response(200, text="User-agent: *\nCrawl-delay: 6"), "a.com/a": HTML, "a.com/b": HTML, "a.com/c": HTML})
    f, ft, *_ = make(engine, site)

    async def go():
        for u in "abc":
            await f.fetch(f"https://a.com/{u}")

    run(go())
    page_times = [t for h, t in site.times][1:]  # skip robots.txt
    gaps = [b - a for a, b in zip(page_times, page_times[1:])]
    assert gaps and all(g >= 6 - 1e-9 for g in gaps)


def test_a_crawl_delay_over_the_cap_skips_the_domain_with_a_reason(engine):
    f, *_ = make(engine, Site({"a.com/robots.txt": httpx.Response(200, text="User-agent: *\nCrawl-delay: 60"), "a.com/": HTML}))
    r = run(f.fetch("https://a.com/"))
    assert r.reason == "crawl_delay_too_long"


# ---- politeness and concurrency ---------------------------------------------------------------

def test_one_request_at_a_time_per_domain_with_the_delay_between(engine):
    site = Site({f"a.com/p{i}": HTML for i in range(5)})
    site.delay = 0.01
    f, ft, *_ = make(engine, site)

    async def go():
        return await asyncio.gather(*[f.fetch(f"https://a.com/p{i}") for i in range(5)])

    res = run(go())
    assert all(r.ok for r in res)
    assert site.max_active == 1  # never two at once on one site
    times = [t for h, t in site.times if h == "a.com"][1:]
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(times, times[1:]))  # at least the minimum delay apart


def test_many_domains_run_in_parallel_up_to_the_global_limit(engine):
    site = Site({f"h{i}.com/": HTML for i in range(12)})
    site.delay = 0.02
    f, ft, *_ = make(engine, site, global_limit=4)

    async def go():
        return await asyncio.gather(*[f.fetch(f"https://h{i}.com/") for i in range(12)])

    res = run(go())
    assert all(r.ok for r in res)
    assert 2 <= site.max_active <= 4 and f.snapshot()["max_inflight"] <= 4


# ---- resilience -------------------------------------------------------------------------------

def test_503_is_retried_with_backoff_then_succeeds(engine):
    calls = iter([httpx.Response(503), httpx.Response(503), None])

    def page(req):
        r = next(calls)
        return r if r is not None else html_response()

    f, ft, *_ = make(engine, Site({"a.com/": page}))
    r = run(f.fetch("https://a.com/"))
    assert r.ok and r.attempts == 3 and f.snapshot()["retries"] == 2
    backoffs = [s for s in ft.sleeps if s in (0.5, 1.0) or 0.5 <= s <= 1.25]
    assert len(backoffs) >= 2


def test_retries_stop_after_the_maximum_and_the_failure_counts_against_the_host(engine):
    f, ft, store, sc = make(engine, Site({"a.com/": httpx.Response(500)}))
    r = run(f.fetch("https://a.com/"))
    assert r.reason == "http_500" and r.attempts == 3  # 1 try + 2 retries
    assert store.get_domain("a.com").fail_count == 1


def test_retry_after_is_honoured(engine):
    seq = iter([httpx.Response(429, headers={"retry-after": "7"}), None])
    f, ft, *_ = make(engine, Site({"a.com/": lambda req: (lambda r: r if r is not None else html_response())(next(seq))}))
    r = run(f.fetch("https://a.com/"))
    assert r.ok and any(s >= 7 - 1e-9 for s in ft.sleeps)


def test_a_huge_retry_after_gives_up_instead_of_waiting(engine):
    f, ft, store, sc = make(engine, Site({"a.com/": httpx.Response(429, headers={"retry-after": "3600"})}))
    r = run(f.fetch("https://a.com/"))
    assert r.reason == "rate_limited" and max(ft.sleeps, default=0) < 30


def test_network_errors_are_retried_then_reported(engine):
    n = {"c": 0}

    def page(req):
        n["c"] += 1
        return httpx.ReadError("reset") if n["c"] < 3 else html_response()

    f, *_ = make(engine, Site({"a.com/": page}))
    assert run(f.fetch("https://a.com/")).ok
    f2, *_ = make(engine, Site({"b.com/": httpx.ReadError("reset")}))
    r = run(f2.fetch("https://b.com/"))
    assert r.reason == "network" and r.attempts == 3


def test_403_is_not_retried_and_trips_the_breaker_after_three(engine):
    site = Site({f"a.com/p{i}": httpx.Response(403) for i in range(6)})
    f, ft, store, sc = make(engine, site)

    async def go():
        return [await f.fetch(f"https://a.com/p{i}") for i in range(5)]

    res = run(go())
    assert [r.reason for r in res[:3]] == ["http_403"] * 3 and all(r.attempts == 1 for r in res[:3])
    assert res[3].reason == "breaker:blocked" and res[4].reason == "breaker:blocked"
    assert not any(r.url.path in ("/p3", "/p4") for r in site.requests)  # the open breaker sent nothing
    assert f.snapshot()["breaker_tripped"] == 1
    sc.t += 3601
    assert store.blocked("a.com") is None  # heals after the cooldown


def test_a_404_does_not_count_as_a_failing_host(engine):
    f, ft, store, sc = make(engine, Site({}))

    async def go():
        for i in range(6):
            await f.fetch(f"https://a.com/guess{i}")

    run(go())
    assert store.blocked("a.com") is None and store.get_domain("a.com").fail_count == 0


def test_dns_failure_marks_the_domain_dead_for_a_week(engine):
    f, ft, store, sc = make(engine, Site({"gone.com/": httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")}))
    r = run(f.fetch("https://gone.com/"))
    assert r.reason == "dns" and r.attempts == 1  # not retried
    assert run(f.fetch("https://gone.com/x")).reason == "dead_domain"
    sc.t += 8 * 86400
    assert store.blocked("gone.com") is None


def test_a_request_that_never_finishes_hits_the_total_deadline(engine):
    async def slow(req):
        await asyncio.sleep(5)

    site = Site({})
    site.delay = 0
    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    ft = FakeTime()
    f = AsyncFetcher(CrawlStore(engine), client=client, check_public=False, sleep=ft.sleep, clock=ft.clock, total_timeout_s=0.1, max_retries=0)
    import time
    t = time.monotonic()
    r = run(f.fetch("https://a.com/"))
    assert r.reason in ("timeout", "robots") and time.monotonic() - t < 2


def test_response_size_is_capped_and_odd_encodings_do_not_crash(engine):
    big = httpx.Response(200, content=b"<html><body>" + b"x" * 5000 + b"</body></html>", headers={"content-type": "text/html"})
    latin = httpx.Response(200, content="<html><body>caf\xe9 ".encode("latin-1") + b"y" * 300 + b"</body></html>", headers={"content-type": "text/html; charset=iso-8859-1"})
    bad_charset = httpx.Response(200, content=b"<html><body>" + b"z" * 300 + b"</body></html>", headers={"content-type": "text/html; charset=made-up"})
    garbage = httpx.Response(200, content=b"<html><body><div><p>\xff\xfe\x00 unclosed <b><i>" + b"w" * 300, headers={"content-type": "text/html"})
    f, *_ = make(engine, Site({"a.com/big": big, "a.com/latin": latin, "a.com/cs": bad_charset, "a.com/junk": garbage}), max_bytes=1000)

    async def go():
        return [await f.fetch(f"https://a.com/{p}") for p in ("big", "latin", "cs", "junk")]

    big_r, latin_r, cs_r, junk_r = run(go())
    assert big_r.ok and len(big_r.html) == 1000 and f.snapshot()["truncated"] >= 1
    assert "café" in latin_r.html and cs_r.ok and junk_r.ok


def test_redirects_are_followed_and_the_final_url_reported(engine):
    site = Site({"a.com/old": httpx.Response(301, headers={"location": "https://www.a.com/new"}), "www.a.com/new": HTML})
    f, *_ = make(engine, site)
    r = run(f.fetch("https://a.com/old"))
    assert r.ok and r.final_url == "https://www.a.com/new"


def test_captcha_and_bot_walls_are_recorded_not_parsed(engine):
    wall = "<html><head><title>Just a moment...</title></head><body><div id='cf-chl'>Checking your browser</div></body></html>"
    f, ft, store, sc = make(engine, Site({"a.com/": wall}))
    r = run(f.fetch("https://a.com/"))
    assert r.reason == "blocked_captcha" and not r.ok
    assert store.get_domain("a.com").fail_count == 1
    assert f.snapshot()["failures"]["blocked_captcha"] == 1


def test_real_pages_that_mention_captcha_are_not_misclassified(engine):
    page = "<html><body>" + "<p>Our services and team. We use reCAPTCHA on forms. Contact info@a.com</p>" * 60 + "</body></html>"
    f, *_ = make(engine, Site({"a.com/": page}))
    assert run(f.fetch("https://a.com/")).ok


def test_private_and_loopback_hosts_are_refused_without_any_request(engine):
    site = Site({})
    f, *_ = make(engine, site, check_public=True)

    async def go():
        return [await f.fetch(u) for u in ("http://127.0.0.1/x", "http://localhost:8765/", "http://169.254.169.254/latest", "ftp://a.com/", "https:///nohost")]

    res = run(go())
    assert [r.reason for r in res[:3]] == ["ssrf"] * 3 and res[3].reason == "bad_url" and res[4].reason == "bad_url"
    assert site.requests == []


def test_the_user_agent_is_honest_and_carries_the_contact_only_when_set():
    assert "Reachout" in afetch.build_user_agent() and "respects robots.txt" in afetch.build_user_agent() and "contact" not in afetch.build_user_agent()
    ua = afetch.build_user_agent("https://example.org/about")
    assert ua.endswith("contact: https://example.org/about)")
    assert "Mozilla" not in ua and "Chrome" not in ua  # no pretending to be a browser


def test_failures_never_raise_and_are_counted_by_reason(engine):
    site = Site({"a.com/ok": HTML, "b.com/": httpx.Response(500), "c.com/": httpx.ReadError("x")})
    f, *_ = make(engine, site)

    async def go():
        return await asyncio.gather(f.fetch("https://a.com/ok"), f.fetch("https://a.com/missing"), f.fetch("https://b.com/"), f.fetch("https://c.com/"))

    res = run(go())
    assert [r.ok for r in res] == [True, False, False, False]
    assert set(f.snapshot()["failures"]) == {"http_404", "http_500", "network"}


def test_browser_tier_reports_unavailable_without_playwright(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "playwright", None)  # makes `import playwright...` raise ImportError
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)

    async def go():
        b = BrowserTier()
        return await b.render("https://a.com/"), b.available, await b.render("https://a.com/")

    first, avail, second = run(go())
    assert first is None and second is None and avail is False
