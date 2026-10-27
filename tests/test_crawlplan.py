from pathlib import Path

import pytest

from backend.core import crawlplan as cp
from backend.core.models import Mode

FX = Path(__file__).parent / "fixtures"
HOME = (FX / "home.html").read_text()
BASE = "https://acme-robotics.com/"


def types(plan): return [(p.page_type, p.url.removeprefix("https://acme-robotics.com")) for p in plan]


@pytest.mark.parametrize("url,anchor,kind", [
    ("https://a.com/", "", "home"), ("https://a.com/contact-us", "", "contact"), ("https://a.com/x", "Get in touch", "contact"),
    ("https://a.com/about", "", "about"), ("https://a.com/our-team", "", "team"), ("https://a.com/leadership", "", "team"),
    ("https://a.com/careers", "", "careers"), ("https://a.com/jobs/ml-intern", "", "careers"), ("https://a.com/investors", "", "investors"),
    ("https://a.com/portfolio", "", "portfolio"), ("https://a.com/pitch", "", "pitch"), ("https://a.com/people", "", "people"),
    ("https://a.com/random-page", "", "other"), ("https://a.com/x", "Meet the team", "team"),
])
def test_page_type_classification(url, anchor, kind):
    assert cp.classify(url, anchor) == kind


def test_links_are_scored_per_mode_and_junk_is_skipped():
    plan = cp.plan_from_links(BASE, HOME, Mode.internship, 10)
    got = types(plan)
    assert got[0] == ("careers", "/careers")
    assert [k for k, _ in got][:3] == ["careers", "contact", "team"]
    urls = [u for _, u in got]
    for junk in ("/blog/2025/launch", "/privacy-policy", "/login", "/products/arm-x1", "/files/brochure.pdf"):
        assert junk not in urls
    assert not any("twitter.com" in p.url or "other.com" in p.url for p in plan)  # same-site links only
    inv = types(cp.plan_from_links(BASE, HOME, Mode.investor, 10))
    assert inv[0][0] == "team"
    assert types(cp.plan_from_links(BASE, HOME, Mode.service, 10))[0][0] == "contact"


def test_the_limit_and_deep_pages_are_respected():
    assert len(cp.plan_from_links(BASE, HOME, Mode.internship, 2)) == 2
    deep = [p for p in cp.plan_from_links(BASE, HOME, Mode.internship, 20) if "founding/story" in p.url]
    assert deep == [] or deep[0].score < 50  # four levels down: barely worth a request


def test_the_search_hit_is_a_candidate_when_it_is_on_the_same_site():
    plan = cp.plan_from_links(BASE, "<p>no links</p>", Mode.internship, 5, hit_url="https://acme-robotics.com/about-us")
    assert types(plan) == [("about", "/about-us")]
    assert cp.plan_from_links(BASE, "<p>x</p>", Mode.internship, 5, hit_url="https://elsewhere.com/team") == []


def test_the_homepage_itself_is_never_a_candidate():
    assert cp.plan_from_links(BASE, '<a href="/">Home</a><a href="https://acme-robotics.com">Home</a>', Mode.service, 5) == []


def test_sitemap_parsing_handles_cdata_index_and_garbage():
    pages, children = cp.parse_sitemap((FX / "sitemap.xml").read_text())
    assert "https://acme-robotics.com/get-in-touch" in pages and "https://evil.com/team" in pages and children == []
    pages, children = cp.parse_sitemap((FX / "sitemap_index.xml").read_text())
    assert pages == [] and children == ["https://acme-robotics.com/sitemap-pages.xml", "https://acme-robotics.com/sitemap-blog.xml"]
    assert cp.parse_sitemap("<html>not a sitemap</html>") == ([], [])
    assert cp.parse_sitemap("") == ([], [])
    cp.parse_sitemap("<loc>" * 100000)  # must not hang or blow up


def test_sitemap_plan_is_same_site_scored_and_ordered():
    pages, _ = cp.parse_sitemap((FX / "sitemap.xml").read_text())
    plan = cp.plan_from_sitemap(BASE, pages, Mode.investor, 5)
    assert types(plan)[0] == ("team", "/leadership")
    urls = [p.url for p in plan]
    assert "https://evil.com/team" not in urls and not any("/blog/" in u or "/products/" in u for u in urls)
    assert any("get-in-touch" in u for u in urls)


def test_blind_guesses_are_few_and_mode_ordered():
    assert [g.url.removeprefix("https://a.com") for g in cp.guess_pages("https://a.com/", Mode.internship)] == ["/careers", "/contact", "/about"]
    assert [g.url.removeprefix("https://a.com") for g in cp.guess_pages("https://a.com/x/y", Mode.investor)] == ["/team", "/contact", "/about"]
    assert len(cp.guess_pages("https://a.com", Mode.service, limit=2)) == 2


def test_merge_plans_dedupes_and_keeps_the_best_first():
    a = [cp.PlannedPage("https://a.com/team", "team", 70), cp.PlannedPage("https://a.com/x", "other", 25)]
    b = [cp.PlannedPage("https://a.com/team/", "team", 90), cp.PlannedPage("https://a.com/contact", "contact", 80)]
    out = cp.merge_plans(a, b, limit=3)
    assert [p.url for p in out] == ["https://a.com/team/", "https://a.com/contact", "https://a.com/x"]  # the higher-scored duplicate wins
