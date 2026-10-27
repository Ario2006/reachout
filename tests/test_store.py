import pytest

from backend.core import store as st
from backend.core.store import CrawlStore


class Clock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cs(engine, clock):
    return CrawlStore(engine, clock)


def test_url_normalisation_makes_equivalent_urls_share_a_cache_entry():
    same = ["https://Acme.com/team", "https://acme.com/team/", "https://acme.com:443/team", "https://acme.com/team#people"]
    assert len({st.url_hash(u) for u in same}) == 1
    assert st.url_hash("https://acme.com/") == st.url_hash("https://acme.com")
    assert st.url_hash("https://acme.com/a?x=1") != st.url_hash("https://acme.com/a?x=2")
    assert st.url_hash("http://acme.com/") != st.url_hash("https://acme.com/")


def test_page_roundtrip_with_compression_and_unicode(cs):
    html = "<html><body>" + "Priya Sharma, José Álvarez — équipe " * 500 + "</body></html>"
    cs.put_page("https://a.com/team", "https://www.a.com/team", 200, "text/html", html, "http", etag='"v1"', last_modified="Mon, 01 Jan 2026 00:00:00 GMT")
    p = cs.get_page("https://a.com/team/")
    assert p and p.html == html and p.final_url == "https://www.a.com/team" and p.etag == '"v1"' and p.tier == "http"
    assert cs.cache_stats()["bytes"] < len(html) / 3  # stored compressed


def test_freshness_follows_the_ttl_and_a_304_restarts_it(cs, clock):
    cs.put_page("https://a.com/", "https://a.com/", 200, "text/html", "<p>x</p>", "http", ttl_s=100)
    assert cs.get_page("https://a.com/").fresh(clock())
    clock.t += 101
    assert not cs.get_page("https://a.com/").fresh(clock())
    cs.touch_page("https://a.com/")
    assert cs.get_page("https://a.com/").fresh(clock())


def test_not_found_is_cached_but_only_briefly(cs):
    cs.put_page("https://a.com/team", "https://a.com/team", 404, "text/html", "", "http", ttl_s=st.DEFAULT_TTL_S)
    p = cs.get_page("https://a.com/team")
    assert p.status == 404 and p.html == "" and p.ttl_s == st.NOT_FOUND_TTL_S


def test_overwrite_replaces_the_entry(cs):
    cs.put_page("https://a.com/", "https://a.com/", 200, "text/html", "old", "http")
    cs.put_page("https://a.com/", "https://a.com/", 200, "text/html", "new", "browser")
    p = cs.get_page("https://a.com/")
    assert (p.html, p.tier) == ("new", "browser") and cs.cache_stats()["pages"] == 1


def test_corrupt_cache_entry_is_a_miss_not_a_crash(cs, engine):
    from sqlalchemy import text
    cs.put_page("https://a.com/", "https://a.com/", 200, "text/html", "ok", "http")
    with engine.begin() as c:
        c.execute(text("UPDATE page_cache SET body = x'deadbeef'"))
    assert cs.get_page("https://a.com/") is None


def test_prune_removes_only_old_pages(cs, clock):
    cs.put_page("https://old.com/", "https://old.com/", 200, "text/html", "x", "http")
    clock.t += 100 * 86400
    cs.put_page("https://new.com/", "https://new.com/", 200, "text/html", "y", "http")
    assert cs.prune_pages(90) == 1 and cs.get_page("https://old.com/") is None and cs.get_page("https://new.com/")


def test_robots_state_roundtrip_and_expiry(cs, clock):
    assert cs.get_domain("a.com").robots_fresh(clock()) is False
    cs.save_robots("a.com", "User-agent: *\nDisallow: /x", 200, 3.0, ["https://a.com/sitemap.xml"])
    d = cs.get_domain("a.com")
    assert d.robots_txt.startswith("User-agent") and d.crawl_delay == 3.0 and d.sitemaps == ["https://a.com/sitemap.xml"]
    assert d.robots_fresh(clock())
    clock.t += st.ROBOTS_TTL_S + 1
    assert not cs.get_domain("a.com").robots_fresh(clock())


def test_circuit_breaker_trips_after_repeated_failures_and_heals(cs, clock):
    assert cs.blocked("a.com") is None
    assert cs.record_failure("a.com", "timeout") is False and cs.record_failure("a.com", "timeout") is False
    assert cs.blocked("a.com") is None
    assert cs.record_failure("a.com", "timeout") is True
    assert cs.blocked("a.com") == "breaker:timeout"
    clock.t += st.BREAKER_COOLDOWN_S + 1
    assert cs.blocked("a.com") is None  # cooled down


def test_a_success_resets_the_failure_count(cs):
    cs.record_failure("a.com", "x"); cs.record_failure("a.com", "x")
    cs.record_success("a.com")
    assert cs.get_domain("a.com").fail_count == 0
    assert cs.record_failure("a.com", "x") is False


def test_negative_caches_for_dead_and_no_mx_domains_expire(cs, clock):
    cs.mark_dead("gone.com", 100)
    assert cs.blocked("gone.com") == "dead_domain"
    clock.t += 101
    assert cs.blocked("gone.com") is None
    assert not cs.no_mx("x.com")
    cs.mark_no_mx("x.com", 50)
    assert cs.no_mx("x.com")
    clock.t += 51
    assert not cs.no_mx("x.com")


def test_per_run_url_state_supports_resume(cs, engine):
    from sqlalchemy import text
    with engine.begin() as c:
        c.execute(text("INSERT INTO search_runs (id, request_text) VALUES (1, 'x'), (2, 'y')"))
    cs.set_url_state(1, "a.com", "https://a.com/", "queued")
    cs.set_url_state(1, "a.com", "https://a.com/team/", "done", tier="http", status=200)
    cs.set_url_state(1, "b.com", "https://b.com/", "failed", reason="timeout")
    cs.set_url_state(2, "a.com", "https://a.com/", "done")
    assert cs.url_states(1) == {"https://a.com/": "queued", "https://a.com/team": "done", "https://b.com/": "failed"}
    assert cs.url_states(1, "a.com") == {"https://a.com/": "queued", "https://a.com/team": "done"}
    cs.set_url_state(1, "a.com", "https://a.com/", "done", tier="cache", status=200)
    assert cs.url_states(1, "a.com")["https://a.com/"] == "done"
    assert cs.url_states(2) == {"https://a.com/": "done"}
