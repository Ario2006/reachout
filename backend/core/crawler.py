"""Polite fetching: robots.txt, per-domain delay, size and time limits, subpage selection."""
from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.lexbor import LexborHTMLParser

from backend.core import domains
from backend.core.discovery import USER_AGENT
from backend.core.models import Mode

log = logging.getLogger("reachout.crawler")

MAX_BYTES = 2_000_000
ROBOTS_AGENT = "Reachout"

# Words in a link's path or text that suggest a contact-bearing page, best first per mode.
_KEYWORDS: dict[Mode, tuple[str, ...]] = {
    Mode.internship: ("career", "job", "intern", "team", "about", "contact", "people", "join", "hiring"),
    Mode.investor: ("team", "people", "partner", "contact", "pitch", "invest", "portfolio", "about", "founder", "submit"),
    Mode.service: ("contact", "about", "team", "founder", "story", "people", "connect", "reach"),
}
_COMMON_PATHS = ("/contact", "/contact-us", "/about", "/about-us", "/team", "/people", "/careers", "/jobs",
                 "/pitch", "/investors", "/portfolio", "/company", "/leadership", "/founders")
_SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|mp4|mp3|docx?|xlsx?|pptx?|css|js)(\?|$)", re.I)


@dataclass
class Page:
    url: str  # final URL after redirects
    html: str
    status: int = 200


class Fetcher:
    """Thread-safe: one shared client, robots cache and per-host request pacing."""

    def __init__(self, timeout_s: float = 15, delay_s: tuple[float, float] = (1.0, 2.0),
                 client: httpx.Client | None = None, check_public: bool = True):
        self.delay_s = delay_s
        self.check_public = check_public
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_s), follow_redirects=True, max_redirects=5,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                     "Accept-Language": "en"},
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    # -- robots.txt (RFC 9309: 4xx = no rules, 5xx or unreachable = disallow all) --
    def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if origin in self._robots:
                return self._robots[origin]
        rp: RobotFileParser | None = RobotFileParser()
        try:
            self._pace(parts.netloc)
            r = self._client.get(origin + "/robots.txt")
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            elif 400 <= r.status_code < 500:
                rp.parse([])  # no rules: everything allowed
            else:
                rp.disallow_all = True
        except httpx.HTTPError:
            rp.disallow_all = True
        with self._lock:
            self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp is not None and rp.can_fetch(ROBOTS_AGENT, url)

    def _pace(self, host: str) -> None:
        """Wait so requests to one host are at least delay_s apart."""
        with self._lock:
            gap = random.uniform(*self.delay_s)
            wait = self._last.get(host, 0.0) + gap - time.monotonic()
            self._last[host] = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str) -> Page | None:
        """Fetch one HTML page, or None (blocked by robots, not HTML, error, too big...)."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        if self.check_public and not domains.is_public_host(parts.hostname):
            log.info("skipping non-public host %s", parts.hostname)
            return None
        if not self.allowed(url):
            return None
        self._pace(parts.netloc)
        try:
            with self._client.stream("GET", url) as r:
                if r.status_code >= 400:
                    return Page(url=str(r.url), html="", status=r.status_code)
                ctype = r.headers.get("content-type", "").lower()
                if ctype and "html" not in ctype and "xml" not in ctype and "text/plain" not in ctype:
                    return None
                final_host = urlsplit(str(r.url)).hostname or ""
                if self.check_public and not domains.is_public_host(final_host):
                    return None
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        del buf[MAX_BYTES:]  # strict cap even when a single chunk overshoots
                        break
                return Page(url=str(r.url), html=bytes(buf).decode(r.encoding or "utf-8", errors="replace"))
        except (httpx.HTTPError, ValueError) as e:
            log.info("fetch failed %s: %s", url, type(e).__name__)
            return None


def _norm(url: str) -> str:
    return urldefrag(url)[0].rstrip("/")


def same_site_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """(absolute url, link text) for links on the same registrable domain."""
    site = domains.registrable(base_url)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in LexborHTMLParser(html).css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")) or _SKIP_EXT.search(href):
            continue
        url = urljoin(base_url, href)
        if urlsplit(url).scheme not in ("http", "https") or domains.registrable(url) != site:
            continue
        key = _norm(url)
        if key in seen:
            continue
        seen.add(key)
        out.append((url, a.text(strip=True)[:80]))
    return out


def pick_pages(root_url: str, root_html: str, mode: Mode, cap: int, hit_url: str | None = None) -> list[str]:
    """Up to cap-1 further pages (the root is page 1): the search hit, keyword links, then common paths."""
    keywords = _KEYWORDS[mode]
    picked: list[str] = []
    seen = {_norm(root_url)}

    def add(u: str) -> None:
        if _norm(u) not in seen and len(picked) < cap - 1:
            seen.add(_norm(u))
            picked.append(u)

    if hit_url and domains.registrable(hit_url) == domains.registrable(root_url):
        add(hit_url)

    scored: list[tuple[int, str]] = []
    for url, text in same_site_links(root_html, root_url):
        hay = (urlsplit(url).path + " " + text).lower()
        for rank, kw in enumerate(keywords):
            if kw in hay:
                scored.append((rank, url))
                break
    for _, url in sorted(scored, key=lambda t: t[0]):
        add(url)

    origin = f"{urlsplit(root_url).scheme}://{urlsplit(root_url).netloc}"
    for path in _COMMON_PATHS:
        add(origin + path)
    return picked


def iter_site_pages(
    start_url: str,
    mode: Mode,
    fetcher: Fetcher,
    cap: int = 6,
    cancelled: Callable[[], bool] = lambda: False,
) -> Iterator[Page]:
    """Yield pages of one site: the homepage first, then likely contact/team pages (<= cap total)."""
    parts = urlsplit(start_url)
    root = f"{parts.scheme}://{parts.netloc}/"
    first = fetcher.get(root)
    if first is None or first.status >= 400 or not first.html:
        # Homepage blocked or broken: fall back to the page the search surfaced.
        first = fetcher.get(start_url)
        if first is None or first.status >= 400 or not first.html:
            return
    yield first
    fetched = 1
    for url in pick_pages(first.url, first.html, mode, cap, hit_url=start_url if _norm(start_url) != _norm(root) else None):
        if fetched >= cap or cancelled():
            return
        page = fetcher.get(url)
        fetched += 1
        if page is not None and page.status < 400 and page.html:
            yield page
