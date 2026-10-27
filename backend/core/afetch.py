"""Async, polite page fetching with a three-tier waterfall: cache -> plain HTTP -> headless browser.

Parallelism comes from many domains at once. Each domain is a gate that allows one request at a time,
spaced by a random 1-2 s delay (raised to the site's robots Crawl-delay). A global semaphore bounds
requests in flight. Failures never raise: every outcome is a `Fetched` with a reason."""
from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.lexbor import LexborHTMLParser

from backend.core import domains
from backend.core.store import DEFAULT_TTL_S, ROBOTS_TTL_S, CachedPage, CrawlStore

log = logging.getLogger("reachout.fetch")

ROBOTS_AGENT = "Reachout"
MAX_BYTES = 2_000_000
MAX_ROBOTS_BYTES = 500_000
MAX_RETRY_AFTER_S = 30.0
ROBOTS_FAILURE_RETRY_S = 600  # an unreachable robots.txt is rechecked after 10 minutes, not 24 hours
_TEXTISH = ("html", "xml", "text/plain")


def build_user_agent(contact: str = "") -> str:
    """An honest User-Agent: names the tool, says it obeys robots.txt, and gives a contact if one is set."""
    base = "Reachout/0.4 (personal outreach research tool; respects robots.txt"
    contact = contact.strip()
    return f"{base}; contact: {contact})" if contact else base + ")"


@dataclass
class Fetched:
    url: str
    final_url: str
    status: int  # 0 when there was no HTTP response
    html: str
    tier: str  # "cache" | "http" | "browser" | "none"
    reason: str | None = None  # None means success. Otherwise why we have no usable page.
    cache: str = "miss"  # "hit" | "revalidated" | "miss"
    origin_tier: str = ""  # for cache hits: the tier that originally produced the page
    attempts: int = 0
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.reason is None and bool(self.html)


@dataclass
class RobotsInfo:
    parser: RobotFileParser | None
    disallow_all: bool = False
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)

    def allowed(self, url: str) -> bool:
        if self.disallow_all:
            return False
        return self.parser is None or self.parser.can_fetch(ROBOTS_AGENT, url)


def parse_robots(txt: str | None, status: int) -> RobotsInfo:
    """RFC 9309: 4xx = no rules (everything allowed); 5xx or unreachable = disallow all."""
    if status == 200 and txt is not None:
        rp = RobotFileParser()
        rp.parse(txt.splitlines())
        delay = rp.crawl_delay(ROBOTS_AGENT) or rp.crawl_delay("*")
        return RobotsInfo(parser=rp, crawl_delay=float(delay) if delay else None, sitemaps=list(rp.site_maps() or []))
    if 400 <= status < 500:
        return RobotsInfo(parser=None)
    return RobotsInfo(parser=None, disallow_all=True)


_BLOCK_MARKERS = ("just a moment...", "cf-chl", "g-recaptcha", "h-captcha", "hcaptcha", "attention required! | cloudflare",
                  "verify you are human", "unusual traffic from your computer", "access denied", "px-captcha")


def detect_block(html: str, visible_len: int) -> str | None:
    """A challenge page served with 200: short content that is mostly a CAPTCHA or bot wall."""
    if visible_len > 1500:
        return None
    low = html[:20_000].lower()
    return "blocked_captcha" if any(m in low for m in _BLOCK_MARKERS) else None


def visible_len(html: str) -> int:
    tree = LexborHTMLParser(html)
    for n in tree.css("script,style,noscript,svg,template"):
        n.decompose()
    body = tree.body
    return len(re.sub(r"\s+", " ", (body.text(separator=" ") if body is not None else "")).strip())


def looks_like_js_shell(html: str) -> bool:
    """Almost no readable text but scripts present: a client-side-rendered shell."""
    if len(html) < 50 or "<script" not in html.lower():
        return False
    return visible_len(html) < 200 and detect_block(html, 0) is None


class BrowserTier:
    """Headless Chromium for JS-only pages. One reused browser, at most `max_pages` open at once, images,
    fonts and media blocked. Optional: if Playwright or its browser is missing it reports unavailable."""

    BLOCKED = {"image", "font", "media", "stylesheet"}

    def __init__(self, max_pages: int = 2, user_agent: str = "", render_timeout_s: float = 15.0):
        self.max_pages, self.user_agent, self.timeout = max_pages, user_agent, render_timeout_s
        self._sem = asyncio.Semaphore(max_pages)
        self._pw = self._browser = self._ctx = None
        self._lock = asyncio.Lock()
        self.available: bool | None = None

    async def _ensure(self) -> bool:
        if self.available is not None:
            return self.available
        async with self._lock:
            if self.available is not None:
                return self.available
            try:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=True)
                self._ctx = await self._browser.new_context(user_agent=self.user_agent or None, java_script_enabled=True)
                await self._ctx.route("**/*", self._route)
                self.available = True
            except Exception as e:  # noqa: BLE001 - missing package or missing browser binary
                log.info("browser tier unavailable: %s", str(e).splitlines()[0][:120] if str(e) else type(e).__name__)
                self.available = False
                await self.aclose()
        return bool(self.available)

    async def _route(self, route) -> None:  # noqa: ANN001
        if route.request.resource_type in self.BLOCKED:
            await route.abort()
        else:
            await route.continue_()

    async def render(self, url: str) -> str | None:
        if not await self._ensure():
            return None
        async with self._sem:
            page = await self._ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=2500)
                except Exception:  # noqa: BLE001 - some pages never go idle; take what is there
                    pass
                return await page.content()
            except Exception as e:  # noqa: BLE001
                log.info("render failed for %s: %s", url, type(e).__name__)
                return None
            finally:
                await page.close()

    async def aclose(self) -> None:
        for obj, meth in ((self._ctx, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    await getattr(obj, meth)()
                except Exception:  # noqa: BLE001
                    pass
        self._pw = self._browser = self._ctx = None


class _Gate:
    __slots__ = ("lock", "next_at")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.next_at = 0.0


class AsyncFetcher:
    def __init__(
        self,
        store: CrawlStore,
        *,
        delay_s: tuple[float, float] = (1.0, 2.0),
        connect_timeout_s: float = 5.0,
        total_timeout_s: float = 15.0,
        max_bytes: int = MAX_BYTES,
        global_limit: int = 30,
        max_retries: int = 2,
        ttl_s: int = DEFAULT_TTL_S,
        max_crawl_delay_s: float = 10.0,
        user_agent: str | None = None,
        client: httpx.AsyncClient | None = None,
        browser: BrowserTier | None = None,
        http2: bool = False,
        check_public: bool = True,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rand: Callable[[float, float], float] = random.uniform,
    ):
        self.store, self.delay_s, self.total_timeout = store, delay_s, total_timeout_s
        self.max_bytes, self.max_retries, self.ttl_s, self.max_crawl_delay = max_bytes, max_retries, ttl_s, max_crawl_delay_s
        self.browser, self.check_public = browser, check_public
        self._sleep, self._clock, self._rand = sleep, clock, rand
        self.user_agent = user_agent or build_user_agent()
        self._client = client or httpx.AsyncClient(
            http2=http2, follow_redirects=True, max_redirects=5,
            timeout=httpx.Timeout(connect=connect_timeout_s, read=total_timeout_s, write=total_timeout_s, pool=total_timeout_s),
            limits=httpx.Limits(max_connections=max(global_limit * 2, 20), max_keepalive_connections=global_limit),
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
                     "Accept-Language": "en"},
        )
        self._owns_client = client is None
        self._sem = asyncio.Semaphore(global_limit)
        self._gates: dict[str, _Gate] = {}
        self._robots_mem: dict[str, RobotsInfo] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._public: dict[str, bool] = {}
        self._clean: set[str] = set()  # hosts known to have no recorded failures (skips a DB write per fetch)
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="store")
        self.stats: Counter[str] = Counter()
        self.inflight = self.max_inflight = 0

    # ---- plumbing ---------------------------------------------------------------------------
    async def _db(self, fn: Callable, *args):  # noqa: ANN001, ANN002
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    def _gate(self, host: str) -> _Gate:
        g = self._gates.get(host)
        if g is None:
            g = self._gates[host] = _Gate()
        return g

    async def _is_public(self, host: str) -> bool:
        if not self.check_public:
            return True
        if host not in self._public:
            self._public[host] = await self._db(domains.is_public_host, host)
        return self._public[host]

    async def aclose(self) -> None:
        if self.browser:
            await self.browser.aclose()
        if self._owns_client:
            await self._client.aclose()
        self._pool.shutdown(wait=False, cancel_futures=True)  # never block shutdown on a slow DNS lookup

    def _delay(self, crawl_delay: float | None) -> float:
        d = self._rand(*self.delay_s)
        return max(d, min(crawl_delay, self.max_crawl_delay)) if crawl_delay else d

    async def _wait_gate(self, gate: _Gate) -> None:
        wait = gate.next_at - self._clock()
        if wait > 0:
            await self._sleep(wait)

    def _fail(self, url: str, reason: str, status: int = 0, tier: str = "none", attempts: int = 0, t0: float | None = None) -> Fetched:
        self.stats[f"fail:{reason.split(':')[0]}"] += 1
        return Fetched(url=url, final_url=url, status=status, html="", tier=tier, reason=reason, attempts=attempts,
                       elapsed=(self._clock() - t0) if t0 is not None else 0.0)

    # ---- robots -------------------------------------------------------------------------------
    async def robots_for(self, host: str, scheme: str = "https") -> RobotsInfo:
        mem = self._robots_mem.get(host)
        if mem is not None:
            return mem
        lock = self._robots_locks.setdefault(host, asyncio.Lock())
        async with lock:
            if host in self._robots_mem:
                return self._robots_mem[host]
            st = await self._db(self.store.get_domain, host)
            if st.robots_fresh(self.store.now()) and st.robots_status is not None:
                info = parse_robots(st.robots_txt, st.robots_status)
                self.stats["robots_cached"] += 1
            else:
                info = await self._fetch_robots(host, scheme)
            self._robots_mem[host] = info
            return info

    async def _fetch_robots(self, host: str, scheme: str) -> RobotsInfo:
        gate = self._gate(host)
        txt: str | None = None
        status = 0
        async with gate.lock:
            await self._wait_gate(gate)
            try:
                async with self._sem, asyncio.timeout(self.total_timeout):
                    r = await self._client.get(f"{scheme}://{host}/robots.txt")
                status = r.status_code
                if status == 200:
                    txt = r.text[:MAX_ROBOTS_BYTES]
            except (httpx.HTTPError, asyncio.TimeoutError, OSError):
                status = 0
            finally:
                gate.next_at = self._clock() + self._rand(*self.delay_s)
        self.stats["robots_fetched"] += 1
        info = parse_robots(txt, status)
        # An unreachable or 5xx robots.txt means "disallow all": recheck soon rather than for a day.
        fresh_for = ROBOTS_TTL_S if status and status < 500 else ROBOTS_FAILURE_RETRY_S
        await self._db(self._save_robots, host, txt, status, info, fresh_for)
        return info

    def _save_robots(self, host: str, txt: str | None, status: int, info: RobotsInfo, fresh_for: int) -> None:
        self.store.save_robots(host, txt, status, info.crawl_delay, info.sitemaps)
        if fresh_for < ROBOTS_TTL_S:  # back-date so it expires in `fresh_for` seconds
            from sqlalchemy import text as sql
            with self.store.engine.begin() as c:
                c.execute(sql("UPDATE domain_state SET robots_fetched_at = :t WHERE host = :h"),
                          {"t": self.store.now() - ROBOTS_TTL_S + fresh_for, "h": host})

    async def sitemaps_for(self, host: str, scheme: str = "https") -> list[str]:
        info = await self.robots_for(host, scheme)
        return info.sitemaps

    # ---- the waterfall ------------------------------------------------------------------------
    async def fetch(self, url: str, *, render_js: bool = True) -> Fetched:
        t0 = self._clock()
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        if p.scheme not in ("http", "https") or not host:
            return self._fail(url, "bad_url", t0=t0)
        if not await self._is_public(host):
            return self._fail(url, "ssrf", t0=t0)
        why = await self._db(self.store.blocked, host)
        if why:
            return self._fail(url, why, t0=t0)

        # (a) cache
        cached = await self._db(self.store.get_page, url)
        if cached and cached.fresh(self.store.now()):
            self.stats["cache_hit"] += 1
            self.stats["tier:cache"] += 1
            return self._from_cache(cached, "hit", t0)

        robots = await self.robots_for(host, p.scheme)
        if not robots.allowed(url):
            return self._fail(url, "robots", t0=t0)
        if robots.crawl_delay and robots.crawl_delay > self.max_crawl_delay:
            return self._fail(url, "crawl_delay_too_long", t0=t0)

        # (b) plain HTTP, with a conditional request if we hold a stale copy
        out = await self._http(url, host, robots.crawl_delay, cached, t0)
        if isinstance(out, Fetched):
            return out
        status, final_url, ctype, html, etag, lastmod, attempts = out

        # (c) browser, only for a client-side-rendered shell
        tier = "http"
        if render_js and self.browser is not None and status == 200 and looks_like_js_shell(html):
            rendered = await self._render(url, host, robots.crawl_delay)
            if rendered and visible_len(rendered) > visible_len(html):
                html, tier, etag, lastmod = rendered, "browser", None, None
        self.stats[f"tier:{tier}"] += 1
        self.stats["cache_miss"] += 1
        await self._db(self.store.put_page, url, final_url, 200, ctype, html, tier, etag, lastmod, self.ttl_s)
        return Fetched(url=url, final_url=final_url, status=200, html=html, tier=tier, attempts=attempts,
                       elapsed=self._clock() - t0)

    def _from_cache(self, c: CachedPage, how: str, t0: float) -> Fetched:
        if c.status >= 400:
            f = self._fail(c.url, f"http_{c.status}", status=c.status, tier="cache", t0=t0)
        elif not c.html:
            f = self._fail(c.url, "non_html", status=c.status, tier="cache", t0=t0)
        else:
            f = Fetched(url=c.url, final_url=c.final_url, status=c.status, html=c.html, tier="cache", elapsed=self._clock() - t0)
        f.cache, f.origin_tier = how, c.tier
        return f

    async def _render(self, url: str, host: str, crawl_delay: float | None) -> str | None:
        gate = self._gate(host)
        async with gate.lock:
            await self._wait_gate(gate)
            try:
                return await self.browser.render(url)  # type: ignore[union-attr]
            finally:
                gate.next_at = self._clock() + self._delay(crawl_delay)

    async def _http(self, url: str, host: str, crawl_delay: float | None, cached: CachedPage | None, t0: float):
        gate = self._gate(host)
        attempts = 0
        async with gate.lock:
            while True:
                await self._wait_gate(gate)
                attempts += 1
                reason, retry_after, result = await self._one_request(url, cached)
                gate.next_at = self._clock() + self._delay(crawl_delay)
                if reason is None and result is not None:
                    status, final_url, ctype, body, etag, lastmod = result
                    return await self._finish_ok(url, host, cached, status, final_url, ctype, body, etag, lastmod, attempts, t0)
                if reason in ("retry", "timeout", "network") and attempts <= self.max_retries:
                    delay = retry_after if retry_after is not None else (0.5 * 2 ** (attempts - 1)) + self._rand(0, 0.25)
                    if delay > MAX_RETRY_AFTER_S:
                        await self._record_failure(host, "rate_limited")
                        return self._fail(url, "rate_limited", status=429, attempts=attempts, t0=t0)
                    self.stats["retries"] += 1
                    gate.next_at = max(gate.next_at, self._clock() + delay)
                    continue
                return await self._finish_fail(url, host, reason or "error", result, attempts, t0)

    async def _one_request(self, url: str, cached: CachedPage | None):
        """One HTTP exchange. Returns (reason, retry_after, result). reason None means a response to classify."""
        headers = {}
        if cached and cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached and cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
        self.stats["requests"] += 1
        counted = False
        try:
            async with self._sem:
                self.inflight += 1
                counted = True
                self.max_inflight = max(self.max_inflight, self.inflight)
                async with asyncio.timeout(self.total_timeout):
                    async with self._client.stream("GET", url, headers=headers) as r:
                        status = r.status_code
                        retry_after = _retry_after(r.headers.get("retry-after"))
                        ctype = r.headers.get("content-type", "").lower()
                        etag, lastmod = r.headers.get("etag"), r.headers.get("last-modified")
                        final_url = str(r.url)
                        if status == 304:
                            return None, None, (304, final_url, ctype, b"", etag, lastmod)
                        if status == 429 or status >= 500:
                            return "retry", retry_after, (status, final_url, "", b"", None, None)
                        if status >= 400 or not (200 <= status < 300):
                            return None, None, (status, final_url, ctype, b"", None, None)
                        if ctype and not any(t in ctype for t in _TEXTISH):
                            return None, None, (status, final_url, ctype, b"", None, None)
                        buf = bytearray()
                        async for chunk in r.aiter_bytes():
                            buf.extend(chunk)
                            if len(buf) > self.max_bytes:
                                del buf[self.max_bytes:]
                                self.stats["truncated"] += 1
                                break
                        self.stats["bytes"] += len(buf)
                        enc = r.encoding or "utf-8"
                        return None, None, (status, final_url, ctype, _decode(bytes(buf), enc), etag, lastmod)
        except asyncio.TimeoutError:
            return "timeout", None, None
        except httpx.ConnectError as e:
            return ("dns" if _is_dns(e) else "network"), None, None
        except (httpx.HTTPError, OSError):
            return "network", None, None
        finally:
            if counted:
                self.inflight -= 1

    async def _finish_ok(self, url, host, cached, status, final_url, ctype, body, etag, lastmod, attempts, t0):  # noqa: ANN001
        """Classify a completed exchange: a Fetched for failures and 304s, or the page tuple for a usable 2xx."""
        if status == 304 and cached is not None:
            await self._db(self.store.touch_page, url)
            await self._record_success(host)
            self.stats["cache_revalidated"] += 1
            self.stats["tier:cache"] += 1
            return self._from_cache(cached, "revalidated", t0)
        textish = (not ctype) or any(t in ctype for t in _TEXTISH)
        if 200 <= status < 300 and textish:
            block = detect_block(body, visible_len(body))
            if block:
                await self._record_failure(host, "captcha")
                return self._fail(url, block, status=status, attempts=attempts, t0=t0)
            await self._record_success(host)
            return status, final_url, ctype, body, etag, lastmod, attempts
        # Anything else is a definite answer: remember it (empty body) so it is never refetched.
        await self._db(self.store.put_page, url, final_url, status, ctype, "", "http", None, None, self.ttl_s)
        self.stats["cache_miss"] += 1
        if status in (401, 403):
            await self._record_failure(host, "blocked")  # the site is refusing us: counts towards the breaker
        else:
            await self._record_success(host)  # a 404 or a PDF: the server answered, the host is healthy
        if 200 <= status < 300:
            return self._fail(url, "non_html", status=status, attempts=attempts, t0=t0)
        return self._fail(url, f"http_{status}", status=status, attempts=attempts, t0=t0)

    async def _finish_fail(self, url, host, reason, result, attempts, t0):  # noqa: ANN001
        status = result[0] if result else 0
        if reason == "dns":
            await self._db(self.store.mark_dead, host)
            self._clean.discard(host)
            return self._fail(url, "dns", attempts=attempts, t0=t0)
        await self._record_failure(host, reason if reason != "retry" else f"http_{status}")
        return self._fail(url, reason if reason != "retry" else f"http_{status}", status=status, attempts=attempts, t0=t0)

    async def _record_failure(self, host: str, reason: str) -> None:
        self._clean.discard(host)
        if await self._db(self.store.record_failure, host, reason):
            self.stats["breaker_tripped"] += 1

    async def _record_success(self, host: str) -> None:
        if host not in self._clean:
            await self._db(self.store.record_success, host)
            self._clean.add(host)

    def snapshot(self) -> dict:
        s = self.stats
        total = s["cache_hit"] + s["cache_revalidated"] + s["cache_miss"]
        return {
            "requests": s["requests"], "retries": s["retries"], "bytes": s["bytes"], "truncated": s["truncated"],
            "tiers": {"cache": s["tier:cache"], "http": s["tier:http"], "browser": s["tier:browser"]},
            "cache_hits": s["cache_hit"] + s["cache_revalidated"], "cache_total": total,
            "cache_hit_rate": round((s["cache_hit"] + s["cache_revalidated"]) / total, 3) if total else 0.0,
            "failures": {k[5:]: v for k, v in s.items() if k.startswith("fail:")},
            "breaker_tripped": s["breaker_tripped"], "max_inflight": self.max_inflight,
        }


def _retry_after(v: str | None) -> float | None:
    if not v:
        return None
    v = v.strip()
    if v.isdigit():
        return float(v)
    try:
        dt = email.utils.parsedate_to_datetime(v)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _is_dns(e: Exception) -> bool:
    m = str(e).lower()
    return any(s in m for s in ("name or service not known", "nodename nor servname", "no address associated", "temporary failure in name resolution", "getaddrinfo"))


def _decode(raw: bytes, enc: str) -> str:
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")
