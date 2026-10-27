"""SQLite-backed crawl state: page cache, robots and domain state (circuit breaker, negative caches),
per-URL run state. Plain synchronous functions; async code calls them through a small thread pool."""
from __future__ import annotations

import hashlib
import json
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy import Engine, text

DEFAULT_TTL_S = 14 * 86400
NOT_FOUND_TTL_S = 2 * 86400  # 404/410 are cached briefly so blind guesses are never repeated
ROBOTS_TTL_S = 24 * 3600
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 3600
DEAD_TTL_S = 7 * 86400
NO_MX_TTL_S = 30 * 86400


def normalize_url(url: str) -> str:
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme.lower()}://{host}{port}{path}" + (f"?{p.query}" if p.query else "")


def url_hash(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()


@dataclass
class CachedPage:
    url: str
    final_url: str
    status: int
    content_type: str
    etag: str | None
    last_modified: str | None
    html: str
    tier: str
    fetched_at: float
    ttl_s: int

    def fresh(self, now: float) -> bool:
        return now - self.fetched_at < self.ttl_s


@dataclass
class DomainState:
    host: str
    robots_txt: str | None = None
    robots_status: int | None = None
    robots_fetched_at: float | None = None
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)
    fail_count: int = 0
    blocked_until: float | None = None
    blocked_reason: str | None = None
    dead_until: float | None = None
    no_mx_until: float | None = None

    def robots_fresh(self, now: float) -> bool:
        return self.robots_fetched_at is not None and now - self.robots_fetched_at < ROBOTS_TTL_S


class CrawlStore:
    def __init__(self, engine: Engine, clock: Callable[[], float] = time.time):
        self.engine, self.now = engine, clock

    # ---- page cache -------------------------------------------------------------------------
    def get_page(self, url: str) -> CachedPage | None:
        with self.engine.connect() as c:
            r = c.execute(text("SELECT url, final_url, status, content_type, etag, last_modified, body, tier, fetched_at, ttl_s "
                               "FROM page_cache WHERE url_hash = :h"), {"h": url_hash(url)}).first()
        if r is None:
            return None
        try:
            html = zlib.decompress(r[6]).decode("utf-8", "replace") if r[6] else ""
        except zlib.error:
            return None  # corrupt entry: treat as a miss
        return CachedPage(url=r[0], final_url=r[1] or r[0], status=r[2], content_type=r[3] or "", etag=r[4],
                          last_modified=r[5], html=html, tier=r[7], fetched_at=r[8], ttl_s=r[9])

    def put_page(self, url: str, final_url: str, status: int, content_type: str, html: str, tier: str,
                 etag: str | None = None, last_modified: str | None = None, ttl_s: int = DEFAULT_TTL_S) -> None:
        if status in (404, 410):
            ttl_s = min(ttl_s, NOT_FOUND_TTL_S)
        body = zlib.compress(html.encode("utf-8", "replace"), 6) if html else None
        with self.engine.begin() as c:
            c.execute(text(
                "INSERT INTO page_cache (url_hash, url, final_url, status, content_type, etag, last_modified, body, body_len, tier, fetched_at, ttl_s) "
                "VALUES (:h, :u, :f, :s, :ct, :e, :lm, :b, :n, :t, :at, :ttl) "
                "ON CONFLICT(url_hash) DO UPDATE SET final_url=excluded.final_url, status=excluded.status, content_type=excluded.content_type, "
                "etag=excluded.etag, last_modified=excluded.last_modified, body=excluded.body, body_len=excluded.body_len, "
                "tier=excluded.tier, fetched_at=excluded.fetched_at, ttl_s=excluded.ttl_s"),
                {"h": url_hash(url), "u": url, "f": final_url, "s": status, "ct": content_type, "e": etag, "lm": last_modified,
                 "b": body, "n": len(html), "t": tier, "at": self.now(), "ttl": ttl_s})

    def touch_page(self, url: str) -> None:
        """A 304 Not Modified: the cached copy is current, restart its TTL."""
        with self.engine.begin() as c:
            c.execute(text("UPDATE page_cache SET fetched_at = :t WHERE url_hash = :h"), {"t": self.now(), "h": url_hash(url)})

    def prune_pages(self, max_age_days: int = 90) -> int:
        with self.engine.begin() as c:
            return c.execute(text("DELETE FROM page_cache WHERE fetched_at < :t"), {"t": self.now() - max_age_days * 86400}).rowcount

    def cache_stats(self) -> dict:
        with self.engine.connect() as c:
            n, size = c.execute(text("SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM page_cache")).one()
        return {"pages": int(n), "bytes": int(size)}

    # ---- domain state: robots, breaker, negative caches ------------------------------------
    def get_domain(self, host: str) -> DomainState:
        with self.engine.connect() as c:
            r = c.execute(text("SELECT robots_txt, robots_status, robots_fetched_at, crawl_delay, sitemaps_json, fail_count, "
                               "blocked_until, blocked_reason, dead_until, no_mx_until FROM domain_state WHERE host = :h"),
                          {"h": host}).first()
        if r is None:
            return DomainState(host=host)
        return DomainState(host=host, robots_txt=r[0], robots_status=r[1], robots_fetched_at=r[2], crawl_delay=r[3],
                           sitemaps=json.loads(r[4] or "[]"), fail_count=r[5], blocked_until=r[6], blocked_reason=r[7],
                           dead_until=r[8], no_mx_until=r[9])

    def _upsert(self, host: str, **cols: object) -> None:
        cols["updated_at"] = self.now()
        names = list(cols)
        with self.engine.begin() as c:
            c.execute(text(f"INSERT INTO domain_state (host, {', '.join(names)}) VALUES (:host, {', '.join(':' + n for n in names)}) "
                           f"ON CONFLICT(host) DO UPDATE SET {', '.join(f'{n} = excluded.{n}' for n in names)}"),
                      {"host": host, **cols})

    def save_robots(self, host: str, robots_txt: str | None, status: int, crawl_delay: float | None, sitemaps: list[str]) -> None:
        self._upsert(host, robots_txt=robots_txt, robots_status=status, robots_fetched_at=self.now(),
                     crawl_delay=crawl_delay, sitemaps_json=json.dumps(sitemaps))

    def record_success(self, host: str) -> None:
        self._upsert(host, fail_count=0, blocked_until=None, blocked_reason=None)

    def record_failure(self, host: str, reason: str, threshold: int = BREAKER_THRESHOLD,
                       cooldown_s: int = BREAKER_COOLDOWN_S) -> bool:
        """Count a failure; after `threshold` in a row the breaker trips for `cooldown_s`. Returns True if it tripped."""
        n = self.get_domain(host).fail_count + 1
        if n >= threshold:
            self._upsert(host, fail_count=n, blocked_until=self.now() + cooldown_s, blocked_reason=reason)
            return True
        self._upsert(host, fail_count=n)
        return False

    def mark_dead(self, host: str, ttl_s: int = DEAD_TTL_S) -> None:
        self._upsert(host, dead_until=self.now() + ttl_s, blocked_reason="dead")

    def mark_no_mx(self, domain: str, ttl_s: int = NO_MX_TTL_S) -> None:
        self._upsert(domain, no_mx_until=self.now() + ttl_s)

    def blocked(self, host: str) -> str | None:
        """Why this host must be skipped right now (dead, breaker open), or None."""
        d, now = self.get_domain(host), self.now()
        if d.dead_until and d.dead_until > now:
            return "dead_domain"
        if d.blocked_until and d.blocked_until > now:
            return f"breaker:{d.blocked_reason or 'failures'}"
        return None

    def no_mx(self, domain: str) -> bool:
        d = self.get_domain(domain)
        return bool(d.no_mx_until and d.no_mx_until > self.now())

    # ---- per-run URL state (resume) ---------------------------------------------------------
    def set_url_state(self, run_id: int, domain: str, url: str, state: str, tier: str | None = None,
                      status: int | None = None, reason: str | None = None) -> None:
        with self.engine.begin() as c:
            c.execute(text(
                "INSERT INTO crawl_urls (run_id, domain, url, state, tier, status, reason, attempts, updated_at) "
                "VALUES (:r, :d, :u, :s, :t, :st, :re, 1, :at) "
                "ON CONFLICT(run_id, url) DO UPDATE SET state=excluded.state, tier=excluded.tier, status=excluded.status, "
                "reason=excluded.reason, attempts=crawl_urls.attempts + 1, updated_at=excluded.updated_at"),
                {"r": run_id, "d": domain, "u": normalize_url(url), "s": state, "t": tier, "st": status, "re": reason, "at": self.now()})

    def url_states(self, run_id: int, domain: str | None = None) -> dict[str, str]:
        sql = "SELECT url, state FROM crawl_urls WHERE run_id = :r" + (" AND domain = :d" if domain else "")
        with self.engine.connect() as c:
            return {u: s for u, s in c.execute(text(sql), {"r": run_id, "d": domain})}
