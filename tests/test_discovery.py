import httpx
import pytest

from backend.core import discovery as d
from backend.core.models import AppSettings, Mode
from backend.core.planner import Plan

H = lambda url, q="q": d.SearchHit(url=url, query=q)  # noqa: E731


def test_rank_dedupes_by_domain_applies_blocklist_and_orders_by_score():
    hits = [H("https://www.acme.ai/about", "q1"), H("https://acme.ai/team", "q2"), H("https://zeta.io/", "q1"),
            H("https://linkedin.com/company/acme", "q1"), H("https://jobs.lever.co/acme", "q1"),
            H("https://github.com/acme", "q1"), H("ftp://x.com", "q1"), H("not a url", "q1"), H("https://acme.ai/x", "q2")]
    ranked = d.rank_candidates(hits, AppSettings())
    assert [(c.domain, c.score) for c in ranked] == [("acme.ai", 2), ("zeta.io", 1)]
    assert ranked[0].url == "https://www.acme.ai/about"


def test_rank_skip_limit_and_user_blocklist():
    hits = [H(f"https://s{i}.com/") for i in range(5)]
    assert [c.domain for c in d.rank_candidates(hits, AppSettings(), skip={"s0.com"}, limit=2)] == ["s1.com", "s2.com"]
    assert d.rank_candidates(hits, AppSettings(domain_blocklist_extra=["s1.com"]))[0].domain == "s0.com"
    assert "s1.com" not in [c.domain for c in d.rank_candidates(hits, AppSettings(domain_blocklist_extra=["s1.com"]))]


def test_run_searches_reports_progress_and_stops_on_cancel(monkeypatch):
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    plan = Plan(mode=Mode.internship, search_queries=["a", "b", "c"])
    seen = []
    hits = d.run_searches(plan, lambda q: [H(f"https://{q}.com", q)], on_query=lambda i, n, q, f: seen.append((i, n, q, f)))
    assert len(hits) == 3 and seen[-1] == (3, 3, "c", 1)
    n = {"c": 0}

    def cancelled():
        n["c"] += 1
        return n["c"] > 1

    assert len(d.run_searches(plan, lambda q: [H("https://x.com")], cancelled=cancelled)) == 1


def test_ddgs_search_retries_then_gives_up(monkeypatch):
    import ddgs

    calls = {"n": 0}

    class Fake:
        def __init__(self, **kw): pass
        def text(self, q, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("rate limited")
            return [{"href": "https://a.com", "title": "A", "body": "b"}, {"title": "no href"}]

    monkeypatch.setattr(ddgs, "DDGS", Fake)
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    out = d.search_ddgs("q", "in-en")
    assert [h.url for h in out] == ["https://a.com"] and calls["n"] == 3

    class Dead(Fake):
        def text(self, q, **kw): raise RuntimeError("always")

    monkeypatch.setattr(ddgs, "DDGS", Dead)
    assert d.search_ddgs("q") == []


def test_searxng_and_searcher_selection(monkeypatch):
    got = {}

    def fake_get(url, params=None, **kw):
        got["url"], got["q"] = url, params["q"]
        return httpx.Response(200, json={"results": [{"url": "https://a.com", "title": "A", "content": "c"}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(d.httpx, "get", fake_get)
    s = AppSettings(search_source="searxng", searxng_url="http://localhost:8080/")
    out = d.make_searcher(s, Plan(mode=Mode.service))("hello")
    assert got == {"url": "http://localhost:8080/search", "q": "hello"} and out[0].url == "https://a.com"

    monkeypatch.setattr(d.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    assert d.search_searxng("http://x", "q") == []


def test_region_mapping():
    assert d.ddgs_region(Plan(mode=Mode.internship, regions=["Bangalore"])) == "in-en"
    assert d.ddgs_region(Plan(mode=Mode.investor, regions=["Narnia", "UK"])) == "uk-en"
    assert d.ddgs_region(Plan(mode=Mode.service)) == "wt-wt"


def test_overpass_candidates(monkeypatch):
    plan = Plan(mode=Mode.service, industries=["fashion", "jewelry"], regions=["Bangalore"])
    assert ("shop", "clothes") in d.osm_categories_for(plan)
    sent = {}

    def fake_post(url, data=None, **kw):
        sent["q"] = data["data"]
        els = [{"tags": {"name": "Shop A", "website": "shopa.in", "contact:email": "Hi@ShopA.in"}},
               {"tags": {"name": "Shop B", "website": "https://facebook.com/shopb"}}, {"tags": {"name": "No site"}},
               {"tags": {"name": "Dup", "website": "https://shopa.in/x"}}]
        return httpx.Response(200, json={"elements": els}, request=httpx.Request("POST", url))

    monkeypatch.setattr(d.httpx, "post", fake_post)
    out = d.overpass_candidates(plan)
    assert [(c.domain, c.osm_email, c.osm_name) for c in out] == [("shopa.in", "Hi@ShopA.in", "Shop A")]
    assert "Bengaluru|Bangalore" in sent["q"] and '"shop"="clothes"' in sent["q"]
    assert d.overpass_candidates(Plan(mode=Mode.service, industries=["quantum"], regions=["X"])) == []
    assert d.overpass_candidates(Plan(mode=Mode.service, industries=["fashion"])) == []


@pytest.mark.parametrize("mode,enabled,expected", [(Mode.service, True, True), (Mode.service, False, False), (Mode.investor, True, False)])
def test_wants_overpass(mode, enabled, expected):
    assert d.wants_overpass(Plan(mode=mode), enabled) is expected


LIST_HTML = """<html><body><h1>Top 20 AI startups in Bangalore</h1>
<ol><li><a href="https://alpha.ai/">Alpha AI</a> - vision</li><li><a href="https://www.beta.io/about">Beta</a></li>
<li><a href="https://linkedin.com/company/gamma">Gamma on LinkedIn</a></li><li><a href="https://alpha.ai/x">Alpha again</a></li>
<li><a href="https://jobs.lever.co/delta">Delta jobs</a></li><li><a href="/internal">Internal</a></li>
<li><a href="https://epsilon.com/">read more</a></li><li><a href="https://zeta.co/brochure.pdf">Zeta</a></li>
<li><a href="https://hiredoor.in/">Hire</a></li></ol><a href="https://self.com/">own site</a></body></html>"""


def hit(url, title="", snippet=""):
    return d.SearchHit(url=url, title=title, snippet=snippet, query="q")


def test_list_hit_detection():
    assert d.is_list_hit(hit("https://x.com", "Top 50 AI startups in Bangalore"))
    assert d.is_list_hit(hit("https://x.com", "", "A list of fintech companies to know"))
    assert d.is_list_hit(hit("https://x.com", "Best VC firms for pre-seed"))
    assert d.is_list_hit(hit("https://x.com", "25 D2C brands you should know"))
    assert not d.is_list_hit(hit("https://acme.ai/careers", "Careers at Acme"))
    assert not d.is_list_hit(hit("https://x.com", "How to build a startup"))


def test_outbound_links_filter_junk():
    links = d.outbound_company_links(LIST_HTML, "https://self.com/best-ai-startups", AppSettings())
    assert [u for u, _ in links] == ["https://alpha.ai/", "https://www.beta.io/about"]


def test_expand_list_pages_uses_only_unblocked_list_hits_and_scores_repeats():
    class P:  # minimal page stand-in
        def __init__(self, url, html): self.url, self.html = url, html

    pages = {"https://blog.com/top-startups": P("https://blog.com/top-startups", LIST_HTML),
             "https://blog2.com/best-startups": P("https://blog2.com/best-startups", '<a href="https://alpha.ai/">A</a><a href="https://omega.io">O</a>')}
    fetched = []
    hits = [hit("https://blog.com/top-startups", "Top 20 startups"), hit("https://blog2.com/best-startups", "Best startups list"),
            hit("https://linkedin.com/pulse/top-startups", "Top startups"),  # blocked: never fetched
            hit("https://acme.ai/about", "About Acme")]  # not a list
    logs = []
    cands = d.expand_list_pages(hits, lambda u: fetched.append(u) or pages.get(u), AppSettings(), on_page=lambda u, n: logs.append((u, n)))
    assert fetched == ["https://blog.com/top-startups", "https://blog2.com/best-startups"]
    assert cands[0].domain == "alpha.ai" and cands[0].score == 2  # linked from two lists
    assert {c.domain for c in cands} == {"alpha.ai", "beta.io", "omega.io", "self.com"} and logs[0][1] == 3


def test_expand_respects_page_cap_and_cancel():
    hits = [hit(f"https://b{i}.com/top-startups", "Top startups") for i in range(20)]
    fetched = []
    d.expand_list_pages(hits, lambda u: fetched.append(u) or None, AppSettings(), max_pages=3)
    assert len(fetched) == 3
    fetched.clear()
    d.expand_list_pages(hits, lambda u: fetched.append(u) or None, AppSettings(), cancelled=lambda: True)
    assert fetched == []


def test_interleave_alternates_and_dedupes():
    C = lambda n: d.Candidate(domain=n, url="https://" + n)  # noqa: E731
    out = d.interleave([C("a"), C("b"), C("c")], [C("x"), C("a"), C("y"), C("z")])
    assert [c.domain for c in out] == ["a", "x", "b", "c", "y", "z"]
    assert [c.domain for c in d.interleave([], [C("q")])] == ["q"]


def test_overpass_falls_back_to_the_next_server(monkeypatch):
    plan = Plan(mode=Mode.service, industries=["jewelry"], regions=["Pune"])
    tried = []

    def fake_post(url, data=None, **kw):
        tried.append(url)
        if len(tried) == 1:
            return httpx.Response(502, text="bad gateway", request=httpx.Request("POST", url))
        return httpx.Response(200, json={"elements": [{"tags": {"name": "Shop", "website": "https://shop.in"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(d.httpx, "post", fake_post)
    assert [c.domain for c in d.overpass_candidates(plan)] == ["shop.in"]
    assert tried == list(d.OVERPASS_URLS)
    tried.clear()
    monkeypatch.setattr(d.httpx, "post", lambda url, **kw: (_ for _ in ()).throw(httpx.ReadTimeout("slow")))
    assert d.overpass_candidates(plan) == []  # every server failed: no candidates, no exception
