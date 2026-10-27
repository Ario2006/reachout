"""Find candidate organisations: web search (ddgs or SearXNG) and, optionally, OpenStreetMap."""
from __future__ import annotations

import logging
import random
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from backend.core import domains
from backend.core.models import AppSettings, Mode
from backend.core.planner import Plan

log = logging.getLogger("reachout.discovery")

USER_AGENT = "Reachout/0.3 (local personal research tool; respects robots.txt)"
RESULTS_PER_QUERY = 15

# ddgs region codes for places users commonly name; anything else searches worldwide.
_DDGS_REGIONS = {
    "india": "in-en", "bangalore": "in-en", "bengaluru": "in-en", "mumbai": "in-en", "delhi": "in-en",
    "hyderabad": "in-en", "pune": "in-en", "chennai": "in-en", "gurgaon": "in-en", "gurugram": "in-en",
    "noida": "in-en", "kolkata": "in-en", "usa": "us-en", "us": "us-en", "united states": "us-en",
    "san francisco": "us-en", "new york": "us-en", "uk": "uk-en", "london": "uk-en",
    "singapore": "sg-en", "sea": "sg-en", "southeast asia": "sg-en", "australia": "au-en",
    "canada": "ca-en", "germany": "de-en", "berlin": "de-en", "uae": "xa-en", "dubai": "xa-en",
}


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    query: str = ""


@dataclass
class Candidate:
    domain: str  # registrable domain
    url: str  # best starting URL (first hit)
    title: str = ""
    score: int = 0  # number of distinct queries that surfaced it
    queries: set[str] = field(default_factory=set)
    # Pre-known contact details (OpenStreetMap tags), used without crawling.
    osm_email: str | None = None
    osm_name: str | None = None


Searcher = Callable[[str], list[SearchHit]]


def ddgs_region(plan: Plan) -> str:
    for r in plan.regions:
        code = _DDGS_REGIONS.get(r.lower())
        if code:
            return code
    return "wt-wt"


def search_ddgs(query: str, region: str = "wt-wt", max_results: int = RESULTS_PER_QUERY,
                retries: int = 2) -> list[SearchHit]:
    from ddgs import DDGS  # imported lazily: heavy, and only needed for real searches

    for attempt in range(retries + 1):
        try:
            rows = DDGS(timeout=15).text(query, region=region, max_results=max_results)
            return [SearchHit(url=r.get("href", ""), title=r.get("title", ""),
                              snippet=r.get("body", ""), query=query) for r in rows if r.get("href")]
        except Exception as e:  # noqa: BLE001 - ddgs raises several exception types on rate limits
            wait = 3 * (attempt + 1) + random.random()
            log.warning("search failed for %r (%s); %s", query, type(e).__name__,
                        f"retrying in {wait:.0f}s" if attempt < retries else "giving up")
            if attempt < retries:
                time.sleep(wait)
    return []


def search_searxng(base_url: str, query: str, max_results: int = RESULTS_PER_QUERY) -> list[SearchHit]:
    try:
        r = httpx.get(base_url.rstrip("/") + "/search", params={"q": query, "format": "json"},
                      headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        return [SearchHit(url=x.get("url", ""), title=x.get("title", ""), snippet=x.get("content", ""),
                          query=query) for x in r.json().get("results", [])[:max_results] if x.get("url")]
    except (httpx.HTTPError, ValueError) as e:
        log.warning("SearXNG search failed for %r: %s", query, e)
        return []


def make_searcher(settings: AppSettings, plan: Plan) -> Searcher:
    if settings.search_source == "searxng" and settings.searxng_url:
        return lambda q: search_searxng(settings.searxng_url, q)
    region = ddgs_region(plan)
    return lambda q: search_ddgs(q, region)


def run_searches(
    plan: Plan,
    searcher: Searcher,
    *,
    on_query: Callable[[int, int, str, int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    pause_s: tuple[float, float] = (2.0, 4.0),
) -> list[SearchHit]:
    hits: list[SearchHit] = []
    queries = plan.search_queries
    for i, q in enumerate(queries):
        if cancelled():
            break
        found = searcher(q)
        hits.extend(found)
        if on_query:
            on_query(i + 1, len(queries), q, len(found))
        if i < len(queries) - 1:
            time.sleep(random.uniform(*pause_s))
    return hits


def rank_candidates(hits: list[SearchHit], settings: AppSettings, *, skip: set[str] | None = None,
                    limit: int | None = None) -> list[Candidate]:
    """One candidate per registrable domain, blocklist applied, best-surfaced domains first."""
    by_domain: OrderedDict[str, Candidate] = OrderedDict()
    for h in hits:
        if not h.url.lower().startswith(("http://", "https://")):
            continue
        d = domains.registrable(h.url)
        if not d or "." not in d or domains.is_blocked(d, settings.domain_blocklist_extra):
            continue
        if skip and d in skip:
            continue
        c = by_domain.get(d)
        if c is None:
            c = by_domain[d] = Candidate(domain=d, url=h.url, title=h.title)
        c.queries.add(h.query)
        c.score = len(c.queries)
    ranked = sorted(by_domain.values(), key=lambda c: -c.score)  # stable: ties keep search order
    return ranked[:limit] if limit else ranked


# ---- OpenStreetMap (service mode, optional) ------------------------------------------------

# Free shared servers: either can be slow or overloaded, so each is tried in turn.
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
OSM_CATEGORIES: dict[str, tuple[str, str]] = {
    "restaurant": ("amenity", "restaurant"), "cafe": ("amenity", "cafe"), "bakery": ("shop", "bakery"),
    "clothing": ("shop", "clothes"), "fashion": ("shop", "clothes"), "apparel": ("shop", "clothes"),
    "jewelry": ("shop", "jewelry"), "jewellery": ("shop", "jewelry"), "furniture": ("shop", "furniture"),
    "beauty": ("shop", "beauty"), "cosmetics": ("shop", "cosmetics"), "salon": ("shop", "hairdresser"),
    "gym": ("leisure", "fitness_centre"), "fitness": ("leisure", "fitness_centre"),
    "hotel": ("tourism", "hotel"), "pet": ("shop", "pet"), "electronics": ("shop", "electronics"),
    "books": ("shop", "books"), "florist": ("shop", "florist"), "sports": ("shop", "sports"),
    "toys": ("shop", "toys"), "bicycle": ("shop", "bicycle"), "optician": ("shop", "optician"),
    "dentist": ("amenity", "dentist"), "clinic": ("amenity", "clinic"), "school": ("amenity", "school"),
}


def osm_categories_for(plan: Plan) -> list[tuple[str, str]]:
    text = " ".join(plan.industries + plan.keywords).lower()
    found: list[tuple[str, str]] = []
    for word, tag in OSM_CATEGORIES.items():
        if word in text and tag not in found:
            found.append(tag)
    return found


def overpass_candidates(plan: Plan, limit: int = 80, timeout_s: float = 100) -> list[Candidate]:
    """Businesses with a website (and sometimes an email) tagged in OpenStreetMap."""
    cats, region = osm_categories_for(plan), (plan.regions[0] if plan.regions else "")
    if not cats or not region:
        return []
    area = region.replace('"', "")
    alt = {"bangalore": "Bengaluru|Bangalore", "bengaluru": "Bengaluru|Bangalore"}.get(area.lower(), area)
    parts = "".join(f'nwr(area.a)["{k}"="{v}"]["website"];' for k, v in cats)
    query = (f'[out:json][timeout:75];area["name"~"^({alt})$"]["boundary"="administrative"]->.a;'
             f"({parts});out tags {limit};")
    elements: list[dict] | None = None
    for url in OVERPASS_URLS:
        try:
            r = httpx.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=timeout_s)
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Overpass query failed on %s: %s", url, e)
    if elements is None:
        return []
    out: dict[str, Candidate] = {}
    for el in elements:
        tags = el.get("tags", {})
        site = tags.get("website") or tags.get("contact:website") or ""
        if not site:
            continue
        if not site.startswith(("http://", "https://")):
            site = "https://" + site
        d = domains.registrable(site)
        if not d or d in out or domains.is_blocked(d):
            continue
        out[d] = Candidate(domain=d, url=site, title=tags.get("name", ""), score=1,
                           osm_email=(tags.get("contact:email") or tags.get("email") or None),
                           osm_name=tags.get("name"))
    return list(out.values())


def wants_overpass(plan: Plan, enabled: bool) -> bool:
    return enabled and plan.mode == Mode.service


# ---- list pages: "Top 50 AI startups in Bangalore" -> the companies they link to ---------------------

_LIST_HIT = re.compile(
    r"\b(top|best|leading|list of|directory|emerging|\d{2,3}\+?)\b.{0,60}\b(start-?ups?|compan(?:y|ies)|firms|investors|vcs?|"
    r"venture|funds|brands|agencies|accelerators|incubators)\b", re.I)
_SKIP_ANCHOR = re.compile(r"^(read more|learn more|click here|here|source|link|website|visit|home|share|tweet|login|sign up|subscribe)$", re.I)


def is_list_hit(hit: SearchHit) -> bool:
    return bool(_LIST_HIT.search(f"{hit.title} {hit.snippet}"))


def outbound_company_links(html: str, page_url: str, settings: AppSettings, limit: int = 40) -> list[tuple[str, str]]:
    """External links on a list page that plausibly point at company sites: (url, anchor text)."""
    own = domains.registrable(page_url)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in LexborHTMLParser(html).css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        d = domains.registrable(href)
        if (not d or d == own or d in seen or "." not in d or domains.is_blocked(d, settings.domain_blocklist_extra)
                or re.search(r"\.(pdf|jpe?g|png|gif|zip)(\?|$)", href, re.I)):
            continue
        text = a.text(strip=True)
        if _SKIP_ANCHOR.match(text) or len(text) > 80:
            continue
        seen.add(d)
        out.append((href, text))
        if len(out) >= limit:
            break
    return out


def expand_list_pages(
    hits: list[SearchHit],
    fetch: Callable[[str], "object | None"],
    settings: AppSettings,
    *,
    max_pages: int = 8,
    per_page: int = 40,
    on_page: Callable[[str, int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[Candidate]:
    """Fetch list-like result pages (not blocked, not job boards) and turn their outbound links into candidates."""
    urls: list[str] = []
    for h in hits:
        d = domains.registrable(h.url)
        if (is_list_hit(h) and d and not domains.is_blocked(d, settings.domain_blocklist_extra)
                and h.url not in urls and len(urls) < max_pages):
            urls.append(h.url)
    found: dict[str, Candidate] = {}
    for url in urls:
        if cancelled():
            break
        page = fetch(url)
        html = getattr(page, "html", "") if page is not None else ""
        if not html:
            continue
        links = outbound_company_links(html, getattr(page, "url", url), settings, per_page)
        for href, text in links:
            d = domains.registrable(href)
            c = found.get(d)
            if c is None:
                found[d] = Candidate(domain=d, url=href, title=text, score=1)
            else:
                c.score += 1  # linked from several lists: more likely a real company
        if on_page:
            on_page(url, len(links))
    return sorted(found.values(), key=lambda c: -c.score)


def interleave(a: list[Candidate], b: list[Candidate]) -> list[Candidate]:
    """Alternate between two ranked lists, dropping repeats, so neither source crowds out the other."""
    out: list[Candidate] = []
    seen: set[str] = set()
    for x, y in zip(a + [None] * max(0, len(b) - len(a)), b + [None] * max(0, len(a) - len(b))):  # type: ignore[operator]
        for c in (x, y):
            if c is not None and c.domain not in seen:
                seen.add(c.domain)
                out.append(c)
    return out


async def expand_list_pages_async(
    hits: list[SearchHit],
    fetch,  # noqa: ANN001 - async callable(url) -> object with .html and .final_url
    settings: AppSettings,
    *,
    max_pages: int = 8,
    per_page: int = 40,
    on_page: Callable[[str, int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> list[Candidate]:
    """expand_list_pages for the async fetcher: list-like result pages become candidate company sites."""
    urls: list[str] = []
    for h in hits:
        d = domains.registrable(h.url)
        if (is_list_hit(h) and d and not domains.is_blocked(d, settings.domain_blocklist_extra)
                and h.url not in urls and len(urls) < max_pages):
            urls.append(h.url)
    found: dict[str, Candidate] = {}
    for url in urls:
        if cancelled():
            break
        page = await fetch(url)
        html = getattr(page, "html", "") if page is not None else ""
        if not html:
            continue
        links = outbound_company_links(html, getattr(page, "final_url", url) or url, settings, per_page)
        for href, text in links:
            d = domains.registrable(href)
            c = found.get(d)
            if c is None:
                found[d] = Candidate(domain=d, url=href, title=text, score=1)
            else:
                c.score += 1
        if on_page:
            on_page(url, len(links))
    return sorted(found.values(), key=lambda c: -c.score)
