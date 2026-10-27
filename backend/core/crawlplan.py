"""Which pages of a site are worth fetching: link scoring, sitemaps, and a last-resort guess list."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from backend.core import domains
from backend.core.models import Mode

# page type -> (pattern on path/anchor text). Order matters: the first match names the type.
_TYPES: list[tuple[str, re.Pattern[str]]] = [
    ("pitch", re.compile(r"pitch|submit|apply|dealflow|startups?[-\s]apply|founders?[-\s]?apply", re.I)),
    ("investors", re.compile(r"investors?|invest[-\s]with|limited[-\s]partners|lps?\b", re.I)),
    ("portfolio", re.compile(r"portfolio|our[-\s]companies|companies|investments?", re.I)),
    ("team", re.compile(r"\bteam\b|our[-\s]team|leadership|management|founders?|who[-\s]we[-\s]are|meet[-\s]the|partners?\b", re.I)),
    ("people", re.compile(r"people|staff|members|crew|humans", re.I)),
    ("careers", re.compile(r"career|jobs?\b|hiring|join[-\s](us|the)|work[-\s]with[-\s]us|openings|internships?|vacanc", re.I)),
    ("contact", re.compile(r"contact|reach[-\s]us|get[-\s]in[-\s]touch|connect|enquir|inquir|talk[-\s]to|say[-\s]hello|hello\b", re.I)),
    ("about", re.compile(r"about|company|our[-\s]story|story|who[-\s]we[-\s]are|mission", re.I)),
]
_SKIP = re.compile(r"/(blog|news|press|privacy|terms|cookie|legal|login|log-in|signin|sign-in|signup|register|cart|checkout|account|"
                   r"products?|collections?|shop|store|tag|category|wp-content|wp-json|feed|search|events?|webinars?|docs?|help|support|faq)(/|$)", re.I)
_SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|mp4|mp3|docx?|xlsx?|pptx?|css|js|xml|txt)(\?|$)", re.I)

# How useful each page type is for each kind of outreach (0 = not worth a request).
WEIGHTS: dict[Mode, dict[str, int]] = {
    Mode.internship: {"careers": 90, "contact": 80, "team": 70, "people": 65, "about": 60, "pitch": 10, "investors": 5, "portfolio": 5},
    Mode.investor: {"pitch": 95, "team": 90, "people": 85, "investors": 70, "contact": 75, "about": 55, "portfolio": 20, "careers": 5},
    Mode.service: {"contact": 90, "about": 70, "team": 65, "people": 60, "careers": 15, "pitch": 5, "investors": 5, "portfolio": 5},
}
# What to try when a site's own links and sitemap offered nothing, best first (at most 3 are fetched).
GUESSES: dict[Mode, list[str]] = {
    Mode.internship: ["/careers", "/contact", "/about"],
    Mode.investor: ["/team", "/contact", "/about"],
    Mode.service: ["/contact", "/about", "/team"],
}
MIN_USEFUL = 20


@dataclass(frozen=True)
class PlannedPage:
    url: str
    page_type: str
    score: int


def classify(url: str, anchor_text: str = "") -> str:
    """The kind of page a URL (and its link text) points at: contact, team, careers, ... or 'other'."""
    path = urlsplit(url).path
    if path in ("", "/"):
        return "home"
    for name, pat in _TYPES:
        if pat.search(path) or (anchor_text and pat.search(anchor_text)):
            return name
    return "other"


def score_page(url: str, anchor_text: str, mode: Mode) -> tuple[int, str]:
    if _SKIP_EXT.search(url) or _SKIP.search(urlsplit(url).path + "/"):
        return 0, "other"
    kind = classify(url, anchor_text)
    depth = len([s for s in urlsplit(url).path.split("/") if s])
    s = WEIGHTS[mode].get(kind, 0)
    return (max(s - 8 * max(depth - 1, 0), 0), kind)  # deep pages are rarely the contact page


def _norm(u: str) -> str:
    return urldefrag(u)[0].rstrip("/")


def links_from_html(base_url: str, html: str) -> list[tuple[str, str]]:
    """(absolute url, anchor text) of same-site links in document order, deduplicated."""
    site = domains.registrable(base_url)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in LexborHTMLParser(html).css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "sms:")):
            continue
        url = urljoin(base_url, href)
        if urlsplit(url).scheme not in ("http", "https") or domains.registrable(url) != site:
            continue
        k = _norm(url)
        if k not in seen:
            seen.add(k)
            out.append((url, a.text(strip=True)[:80]))
    return out


def plan_from_links(base_url: str, html: str, mode: Mode, limit: int, hit_url: str | None = None) -> list[PlannedPage]:
    """Best pages found from the homepage's own links (and the search hit), highest value first."""
    cands: dict[str, PlannedPage] = {}
    root = _norm(base_url)
    sources = links_from_html(base_url, html)
    if hit_url and domains.registrable(hit_url) == domains.registrable(base_url):
        sources.insert(0, (hit_url, ""))
    for url, text in sources:
        s, kind = score_page(url, text, mode)
        k = _norm(url)
        if s >= MIN_USEFUL and k != root and (k not in cands or s > cands[k].score):
            cands[k] = PlannedPage(url, kind, s)
    return sorted(cands.values(), key=lambda c: -c.score)[:limit]


_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)\s*(?:\]\]>)?\s*</loc>", re.I)


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """(page urls, child sitemap urls) from a sitemap or sitemap index. Regex, so hostile XML cannot hurt us."""
    xml = xml[:1_500_000]
    is_index = "<sitemapindex" in xml[:2000].lower()
    locs = _LOC.findall(xml)
    return ([], locs) if is_index else (locs, [])


def plan_from_sitemap(base_url: str, urls: list[str], mode: Mode, limit: int) -> list[PlannedPage]:
    site = domains.registrable(base_url)
    root = _norm(base_url)
    out: dict[str, PlannedPage] = {}
    for u in urls[:5000]:
        if domains.registrable(u) != site or _norm(u) == root:
            continue
        s, kind = score_page(u, "", mode)
        if s >= MIN_USEFUL and (_norm(u) not in out or s > out[_norm(u)].score):
            out[_norm(u)] = PlannedPage(u, kind, s)
    return sorted(out.values(), key=lambda c: -c.score)[:limit]


def guess_pages(base_url: str, mode: Mode, limit: int = 3) -> list[PlannedPage]:
    p = urlsplit(base_url)
    origin = f"{p.scheme}://{p.netloc}"
    return [PlannedPage(origin + path, classify(origin + path), 1) for path in GUESSES[mode][:limit]]


def merge_plans(*plans: list[PlannedPage], limit: int) -> list[PlannedPage]:
    best: dict[str, PlannedPage] = {}
    for pl in plans:
        for c in pl:
            k = _norm(c.url)
            if k not in best or c.score > best[k].score:
                best[k] = c
    return sorted(best.values(), key=lambda c: -c.score)[:limit]
