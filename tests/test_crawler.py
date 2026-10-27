import time

import httpx
import pytest

from backend.core import crawler
from backend.core.models import Mode

HOME = '<html><body><a href="/team">Our team</a><a href="/careers">Careers</a><a href="/blog/a">Blog</a></body></html>'


def make_fetcher(routes: dict, delay=(0, 0), check_public=False, seen: list | None = None) -> crawler.Fetcher:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(req.url))
        r = routes.get(str(req.url))
        if r is None:
            return httpx.Response(404, text="nope")
        if isinstance(r, httpx.Response):
            return r
        if isinstance(r, Exception):
            raise r
        return httpx.Response(200, text=r, headers={"content-type": "text/html; charset=utf-8"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return crawler.Fetcher(client=client, delay_s=delay, check_public=check_public)


def test_robots_disallow_blocks_fetch_and_never_requests_the_page():
    seen: list = []
    f = make_fetcher({"https://a.com/robots.txt": "User-agent: *\nDisallow: /team", "https://a.com/team": "T"}, seen=seen)
    assert f.get("https://a.com/team") is None
    assert "https://a.com/team" not in seen


def test_robots_specific_agent_and_allow():
    f = make_fetcher({"https://a.com/robots.txt": "User-agent: Reachout\nDisallow: /\n", "https://a.com/": "hi"})
    assert f.get("https://a.com/") is None
    f2 = make_fetcher({"https://a.com/robots.txt": "User-agent: Googlebot\nDisallow: /\n", "https://a.com/": "hi"})
    assert f2.get("https://a.com/").html == "hi"


@pytest.mark.parametrize("robots,allowed", [
    (httpx.Response(404), True), (httpx.Response(403), True), (httpx.Response(410), True),
    (httpx.Response(500), False), (httpx.Response(503), False), (httpx.ConnectError("down"), False),
])
def test_robots_status_handling_rfc9309(robots, allowed):
    f = make_fetcher({"https://a.com/robots.txt": robots, "https://a.com/": "page"})
    assert (f.get("https://a.com/") is not None) is allowed


def test_robots_fetched_once_per_origin():
    seen: list = []
    f = make_fetcher({"https://a.com/x": "1", "https://a.com/y": "2"}, seen=seen)
    f.get("https://a.com/x"); f.get("https://a.com/y")
    assert seen.count("https://a.com/robots.txt") == 1


def test_non_html_and_errors_are_skipped():
    f = make_fetcher({
        "https://a.com/data.json": httpx.Response(200, text="{}", headers={"content-type": "application/json"}),
        "https://a.com/boom": httpx.ReadTimeout("slow"),
    })
    assert f.get("https://a.com/data.json") is None
    assert f.get("https://a.com/boom") is None
    p = f.get("https://a.com/missing")
    assert p is not None and p.status == 404 and p.html == ""


def test_non_http_schemes_and_private_hosts_rejected_without_requests():
    seen: list = []
    f = make_fetcher({}, check_public=True, seen=seen)
    for url in ("ftp://a.com/x", "file:///etc/passwd", "http://127.0.0.1/admin", "http://localhost:8765/", "http://169.254.169.254/latest"):
        assert f.get(url) is None
    assert seen == []


def test_size_cap_truncates(monkeypatch):
    monkeypatch.setattr(crawler, "MAX_BYTES", 1000)
    f = make_fetcher({"https://a.com/big": "x" * 50_000})
    p = f.get("https://a.com/big")
    assert p is not None and len(p.html) == 1000


def test_per_host_pacing():
    stamps: list[float] = []
    f = make_fetcher({"https://a.com/x": "1", "https://a.com/y": "2", "https://b.com/z": "3"}, delay=(0.15, 0.15))
    f.get("https://a.com/x")  # also fetches robots first (paced)
    t0 = time.monotonic()
    f.get("https://a.com/y")
    stamps.append(time.monotonic() - t0)
    assert stamps[0] >= 0.12


def test_iter_site_pages_order_cap_and_selection():
    seen: list = []
    routes = {"https://a.com/": HOME, "https://a.com/team": "<p>team</p>", "https://a.com/careers": "<p>c</p>",
              "https://a.com/contact": "<p>k</p>", "https://a.com/about": "<p>a</p>"}
    f = make_fetcher(routes, seen=seen)
    pages = list(crawler.iter_site_pages("https://a.com/some/article", Mode.internship, f, cap=4))
    urls = [p.url for p in pages]
    assert urls[0] == "https://a.com/"
    fetched = [u for u in seen if not u.endswith("robots.txt")]
    assert len(fetched) <= 4
    assert "https://a.com/blog/a" not in fetched  # no keyword match


def test_iter_site_pages_cap_one_fetches_only_the_homepage():
    seen: list = []
    f = make_fetcher({"https://a.com/": HOME}, seen=seen)
    assert len(list(crawler.iter_site_pages("https://a.com/", Mode.service, f, cap=1))) == 1
    assert [u for u in seen if not u.endswith("robots.txt")] == ["https://a.com/"]


def test_falls_back_to_search_result_url_when_homepage_fails():
    f = make_fetcher({"https://a.com/about-us": "<p>hello info@a.com</p>"})
    pages = list(crawler.iter_site_pages("https://a.com/about-us", Mode.service, f, cap=3))
    assert [p.url for p in pages] == ["https://a.com/about-us"]


def test_unreachable_site_yields_nothing():
    f = make_fetcher({"https://a.com/robots.txt": httpx.Response(500)})
    assert list(crawler.iter_site_pages("https://a.com/", Mode.service, f)) == []


def test_cancel_stops_between_pages():
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 1

    f = make_fetcher({"https://a.com/": HOME, "https://a.com/team": "t", "https://a.com/careers": "c"})
    assert len(list(crawler.iter_site_pages("https://a.com/", Mode.internship, f, cap=6, cancelled=cancelled))) <= 2


def test_pick_pages_mode_preferences_and_same_site_only():
    html = '<a href="/careers">Careers</a><a href="/portfolio">Portfolio</a><a href="https://other.com/team">Team</a><a href="/x.pdf">Team</a>'
    internship = crawler.pick_pages("https://a.com/", html, Mode.internship, 3)
    investor = crawler.pick_pages("https://a.com/", html, Mode.investor, 3)
    assert internship[0] == "https://a.com/careers"
    assert investor[0] == "https://a.com/portfolio"
    assert all("other.com" not in u and not u.endswith(".pdf") for u in internship + investor)
