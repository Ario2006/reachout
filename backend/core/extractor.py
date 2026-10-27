"""Pull contact emails, people and context out of crawled pages."""
from __future__ import annotations

import html as htmllib
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from backend.core import domains, llm
from backend.core.models import AppSettings, EmailConfidence

log = logging.getLogger("reachout.extractor")


@dataclass
class Page:
    """A fetched page, as the extractor sees it."""
    url: str
    html: str
    status: int = 200

CONTEXT_CHARS = 300
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_AT = re.compile(r"\s*[\[\(\{<]\s*(?:at|AT|@)\s*[\]\)\}>]\s*")
_DOT = re.compile(r"\s*[\[\(\{<]\s*(?:dot|DOT|\.)\s*[\]\)\}>]\s*")
_WORD_AT_DOT = re.compile(
    r"\b([A-Za-z0-9._%+\-]{2,})\s+(?:at|AT)\s+([A-Za-z0-9\-]{2,}(?:\s+(?:dot|DOT)\s+[A-Za-z0-9\-]{2,})+)\b"
)
_TEAM_PATH = re.compile(r"/(team|people|about|leadership|founders?|investors?|partners?|portfolio|our-team|company|management|who-we-are|contact)", re.I)
# Job listings mention "engineer" and "manager" constantly: never treat them as people pages.
_JOBISH_PATH = re.compile(r"/(jobs?|careers?|internships?|openings?|vacanc\w*|apply|positions?|hiring|opportunit\w*|roles?|work-with-us|join)(/|$|-)", re.I)
_ROLE_WORDS = re.compile(
    r"\b(founder|co-founder|cofounder|ceo|cto|coo|cfo|cmo|general partner|managing partner|managing director|"
    r"partner|principal|vp |vice president|president|investor|chairman|chief)\b", re.I)
PEOPLE_PROMPT_CHARS = 7000  # ~1,750 tokens: team pages list people near the top, and prefill dominates the cost
_NAME_STOP = frozenset({
    "head", "of", "and", "the", "at", "in", "lead", "manager", "director", "engineer", "designer", "analyst", "associate",
    "svp", "evp", "senior", "junior", "global", "regional", "vice", "managing", "general", "assistant", "advisor",
    "customer", "customers", "support", "booking", "bookings", "for", "sales", "service", "enquiry", "enquiries", "inquiry",
    "contact", "helpdesk", "desk", "office", "admin", "general", "query", "queries", "feedback", "complaints", "press",
    "relations", "recruitment", "co", "co-founder", "cofounder", "founder", "founders", "ceo", "cto", "coo", "cfo", "cmo",
    "chairman", "chairperson", "chair", "chief", "executive", "officer", "president", "partner", "principal", "md", "vp",
    "mr", "mrs", "ms", "dr", "prof", "shri", "smt", "questions", "question", "inquiries", "enquiries", "opportunities",
    "opportunity", "wholesale", "influencer", "influencers", "api", "edi", "retail", "returns", "shipping", "orders", "order",
    "partnerships", "affiliate", "affiliates", "general", "technical", "billing", "reports", "report", "requests", "request",
    "developer", "engineer", "engineers", "intern", "interns", "internship", "manager", "analyst", "executive",
    "designer", "consultant", "specialist", "officer", "lead", "senior", "junior", "trainee", "fresher",
    "apprentice", "retail", "retails", "health", "digital", "software", "systems", "frontend", "backend",
    "fullstack", "full-stack", "stack", "data", "scientist", "remote", "hybrid", "bangalore", "bengaluru",
    "india", "mumbai", "delhi", "hyderabad", "pune", "chennai", "job", "jobs", "career", "careers", "hiring",
    "vacancy", "opening", "role", "position", "apply", "salary", "stipend", "experience", "years", "months",
    "intelligence", "learning", "machine", "artificial", "analytics", "cloud", "mobile", "web", "app", "tech",
    "global", "international", "consulting", "enterprises", "industries", "foods", "motors", "bank", "finance",
    "media", "studio", "studios", "agency", "network", "networks", "systems", "innovations", "robotics",
    "team", "our", "contact", "about", "careers", "join", "read", "more", "learn", "view", "see", "get",
    "meet", "the", "and", "company", "email", "phone", "home", "blog", "news", "login", "sign", "privacy",
    "terms", "all", "rights", "reserved", "copyright", "inc", "ltd", "llc", "pvt", "private", "limited",
    "capital", "ventures", "partners", "fund", "labs", "technologies", "solutions", "services", "group",
})


_TITLE_HINT_RE = re.compile(r"\b(founder|co-?founder|ceo|cto|coo|cfo|chief|president|partner|principal|director|head of|vp|vice president)\b", re.I)


class Person(BaseModel):
    name: str
    role: str = ""


class People(BaseModel):
    people: list[Person] = Field(default_factory=list, max_length=15)


@dataclass
class EmailHit:
    email: str
    context: str
    via: str  # "mailto" | "text" | "cfemail"


@dataclass
class LeadDraft:
    email: str
    name: str = ""
    role: str = ""
    company: str = ""
    domain: str = ""
    source_url: str = ""
    context_snippet: str = ""
    email_confidence: EmailConfidence = EmailConfidence.published_generic
    notes: str = ""
    # Provenance and traceability (milestone 2b)
    page_tier: str = ""
    page_type: str = ""
    fetched_at: float = 0.0
    trace_id: str = ""
    trace: list[tuple[str, str]] = field(default_factory=list)  # (kind, message): why this lead exists
    also: list[dict] = field(default_factory=list)  # other pages that showed the same address: {url, tier, type, fetched_at, snippet}
    mx_ok: bool | None = None  # None = not checked here


@dataclass
class PageResult:
    emails_found: int = 0
    drafts: list[LeadDraft] = field(default_factory=list)
    people: list[tuple[Person, str]] = field(default_factory=list)  # (person, context snippet)
    llm_calls: int = 0
    needs_llm: bool = False


# ---- text ---------------------------------------------------------------------------------

def visible_text(html: str) -> str:
    tree = LexborHTMLParser(html)
    for n in tree.css("script,style,noscript,svg,template"):
        n.decompose()
    body = tree.body
    text = body.text(separator=" ") if body is not None else tree.text(separator=" ")
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def main_text(html: str, limit: int = llm.MAX_PROMPT_CHARS) -> str:
    """Readable page text for the model: trafilatura (favoring recall), else visible text."""
    try:
        import trafilatura

        extracted = trafilatura.extract(html, favor_recall=True, include_comments=False,
                                        include_tables=True, no_fallback=False)
    except Exception:  # noqa: BLE001 - trafilatura can fail on malformed markup
        extracted = None
    text = re.sub(r"\s+", " ", extracted or "").strip()
    if len(text) < 200:  # trafilatura drops boilerplate such as footers; keep everything instead
        text = visible_text(html)
    return text[:limit]


def page_title(html: str) -> str:
    tree = LexborHTMLParser(html)
    og = tree.css_first('meta[property="og:site_name"]')
    if og and og.attributes.get("content"):
        return og.attributes["content"].strip()
    t = tree.css_first("title")
    if t and t.text(strip=True):
        return re.split(r"\s[|\-–—·:]\s", t.text(strip=True))[0].strip()
    return ""


_PAGE_TITLES = frozenset({
    "home", "homepage", "about", "about us", "contact", "contact us", "careers", "career", "jobs", "team", "our team",
    "welcome", "index", "main", "login", "blog", "news", "services", "products", "pricing", "faq", "privacy policy",
    "terms", "404", "not found", "untitled", "just a moment", "access denied",
})


def company_name(html: str, domain: str) -> str:
    """Site name from og:site_name or the title's first segment; the domain's label if that is a page name."""
    title = re.sub(r"^(careers|jobs|about|contact|join|work)( us)?\s+(at|with)\s+", "", page_title(html), flags=re.I).strip()
    if title and 2 <= len(title) <= 60 and title.lower() not in _PAGE_TITLES:
        return title
    label = domain.split(".")[0]
    return label.replace("-", " ").title()


def _window(text: str, start: int, end: int, before: int = 120, after: int = 180) -> str:
    lo, hi = max(0, start - before), min(len(text), end + after)
    snippet = text[lo:hi]
    if lo > 0 and " " in snippet:
        snippet = snippet.split(" ", 1)[1]
    if hi < len(text) and " " in snippet:
        snippet = snippet.rsplit(" ", 1)[0]
    return snippet.strip()[:CONTEXT_CHARS]


# ---- emails -------------------------------------------------------------------------------

def deobfuscate(text: str) -> str:
    """name [at] domain [dot] com  ->  name@domain.com (also the plain-words form)."""
    text = text.replace("＠", "@")
    text = _AT.sub("@", text)
    text = _DOT.sub(".", text)

    def words(m: re.Match[str]) -> str:
        host = re.sub(r"\s+(?:dot|DOT)\s+", ".", m.group(2))
        return f"{m.group(1)}@{host}"

    return _WORD_AT_DOT.sub(words, text)


def decode_cfemail(hexstr: str) -> str | None:
    """Cloudflare 'email protection': first byte is an XOR key for the rest."""
    try:
        raw = bytes.fromhex(hexstr)
        key = raw[0]
        return bytes(b ^ key for b in raw[1:]).decode("utf-8")
    except (ValueError, IndexError):
        return None


def _clean_email(raw: str) -> str | None:
    email = unquote(raw).strip().strip(".,;:<>()[]\"'").lower()
    if "?" in email:
        email = email.split("?", 1)[0]
    return email if domains.looks_like_email(email) else None


def _anchor_context(a) -> str:  # noqa: ANN001 - a selectolax node
    """Text around a link: climb to a container with enough words (a name is often in a sibling heading),
    then take the stretch around the link's own text."""
    own = re.sub(r"\s+", " ", a.text(separator=" ")).strip()
    node, text = a, own
    for _ in range(3):
        if len(text) >= 80 or node.parent is None:
            break
        node = node.parent
        text = re.sub(r"\s+", " ", node.text(separator=" ")).strip()
    if len(text) <= CONTEXT_CHARS:
        return text
    i = text.find(own) if own else -1
    return _window(text, max(i, 0), max(i, 0) + len(own))


def extract_emails(html: str) -> list[EmailHit]:
    """Deduplicated addresses from mailto links, Cloudflare-protected links and visible text."""
    tree = LexborHTMLParser(html)
    hits: dict[str, EmailHit] = {}

    def add(email: str | None, context: str, via: str) -> None:
        if email and email not in hits:
            hits[email] = EmailHit(email=email, context=context[:CONTEXT_CHARS], via=via)

    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        ctx = _anchor_context(a)
        if href.lower().startswith("mailto:"):
            for part in href[7:].split(","):
                add(_clean_email(part), ctx, "mailto")
        elif "/cdn-cgi/l/email-protection#" in href:
            add(_clean_email(decode_cfemail(href.split("#", 1)[1]) or ""), ctx, "cfemail")
    for el in tree.css("[data-cfemail]"):
        decoded = decode_cfemail(el.attributes.get("data-cfemail") or "")
        parent = el.parent if el.parent is not None else el
        add(_clean_email(decoded or ""), re.sub(r"\s+", " ", parent.text(separator=" ")).strip(), "cfemail")

    text = deobfuscate(visible_text(html))
    for m in _EMAIL_IN_TEXT.finditer(text):
        add(_clean_email(m.group(0)), _window(text, m.start(), m.end()), "text")
    return list(hits.values())


# ---- people -------------------------------------------------------------------------------

# Pages whose people belong to *other* organisations (portfolios, lists, news, guests), never the site's own team.
_OTHERS_PATH = re.compile(r"/(portfolio|startups?|companies|company-list|investments?|news|blog|press|media|podcasts?|guests?|speakers?|"
                          r"events?|stories|case-stud\w*|network|directory|top-[\w-]+|best-[\w-]+|list-of-[\w-]+|alumni|members)(/|$|-)", re.I)
_OWN_TEAM_PATH = re.compile(r"/(team|people|leadership|management|founders?|our-team|who-we-are|about|about-us|company|investors?|partners?)(/|$|-)", re.I)

# Words a job title is made of. A role containing anything else (an organisation, a place, a project)
# is not the person's role *here*, e.g. "Co-Founder & CEO of Cognition" or "Managing Director, Mayfield Fund".
_TITLE_WORDS = frozenset({
    "founder", "co-founder", "cofounder", "founders", "ceo", "cto", "coo", "cfo", "cmo", "cpo", "chro", "cio", "chief",
    "executive", "officer", "president", "vice", "vp", "svp", "evp", "head", "director", "managing", "partner",
    "general", "principal", "associate", "chairman", "chairwoman", "chairperson", "chair", "board", "member", "of",
    "the", "and", "&", "technology", "technical", "operating", "financial", "marketing", "product", "engineering",
    "operations", "growth", "strategy", "research", "scientist", "lead", "senior", "junior", "group", "global",
    "investment", "investments", "investor", "angel", "venture", "platform", "data", "ai", "ml", "design", "business",
    "development", "people", "talent", "hr", "sales", "revenue", "customer", "success", "commercial", "innovation",
    "managing-partner", "owner", "md", "gp", "ir", "software", "security", "information", "analytics", "intelligence",
})


def role_is_title_only(role: str) -> bool:
    words = [w for w in re.split(r"[\s,/|•·–—]+", role.lower().replace("&", " & ")) if w]
    words = [w.strip(".()") for w in words if not re.fullmatch(r"[-&.]+", w)]  # bare dashes and ampersands are punctuation
    return bool(words) and all(w in _TITLE_WORDS for w in words)


def guessable_source(url: str) -> bool:
    """Only a page that lists the site's *own* people may justify guessing an address at that domain."""
    path = urlsplit(url).path + "/"
    return bool(_OWN_TEAM_PATH.search(path)) and not _OTHERS_PATH.search(path) and not jobish(url)


def jobish(url: str) -> bool:
    return bool(_JOBISH_PATH.search(urlsplit(url).path + "/"))


def team_like(url: str, text: str) -> bool:
    if jobish(url) or _OTHERS_PATH.search(urlsplit(url).path + "/"):
        return False  # job listings and portfolio/list/news pages: their people are not this site's team
    return bool(_TEAM_PATH.search(urlsplit(url).path)) or len(_ROLE_WORDS.findall(text)) >= 3


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _valid_name(name: str, text_low: str) -> str | None:
    name = re.sub(r"\s+", " ", name).strip(" ,.;:-–—")
    tokens = name.split(" ")
    if not 2 <= len(tokens) <= 4 or len(name) > 60:
        return None
    for t in tokens:
        if not re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.\-]*", t) or t.lower().strip(".") in _NAME_STOP:
            return None
    if not tokens[0][0].isupper():
        return None
    return name if name.lower() in text_low else None  # the name must literally appear on the page


PeopleFn = Callable[[str, str], list[Person]]


def llm_people_fn(settings: AppSettings) -> PeopleFn:
    system = (
        "Extract the real people (founders, executives, partners, team members) named on this web page. "
        "For each give their full name and their job title exactly as written on the page (empty string "
        "if none). Do NOT include company names, job titles, job postings, products, or places. Only "
        "include people who appear in the text; never invent names. Return at most 15."
    )

    def run(text: str, domain: str) -> list[Person]:
        res = llm.generate_json(
            People, system, f"Website: {domain}\n\nPAGE TEXT:\n{text}",
            model=settings.ollama_model, num_ctx=settings.num_ctx, keep_alive=settings.keep_alive,
            max_chars=PEOPLE_PROMPT_CHARS, num_predict=500,
        )
        return res.people

    return run


def find_people(text: str, domain: str, fn: PeopleFn) -> list[Person]:
    low = text.lower()
    out: list[Person] = []
    seen: set[str] = set()
    for p in fn(llm.truncate(text, PEOPLE_PROMPT_CHARS), domain):
        name = _valid_name(p.name, low)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(Person(name=name, role=re.sub(r"\s+", " ", p.role).strip()[:80]))
    return out


_CAP_NAME = re.compile(r"\b([A-ZÀ-Þ][a-zà-ÿ'’\-]{1,}(?:\s+[A-ZÀ-Þ][a-zà-ÿ'’\-.]{0,}){1,2})\b")


def match_full_name(local: str, person: Person) -> bool:
    """The address uses BOTH the first and last name (priya.sharma@, psharma@, sharma.p@), not just one word.
    One word alone ("edi@" beside "EDI questions") is too weak to call a heading a person."""
    local = re.sub(r"[+].*$", "", local).replace("_", ".").replace("-", ".")
    toks = [t for t in re.split(r"[\s\-]+", _ascii(person.name).replace(".", "")) if t]
    if len(toks) < 2:
        return False
    first, last = toks[0], toks[-1]
    return local in {f"{first}.{last}", f"{first}{last}", f"{first[0]}.{last}", f"{first[0]}{last}", f"{first}.{last[0]}",
                     f"{first}{last[0]}", f"{last}.{first}", f"{last}{first}", f"{last}.{first[0]}"}


def people_near_email(context: str, local: str) -> Person | None:
    """A capitalised name in the text around an address that the address is built from
    (priya.sharma@ beside "Priya Sharma"). No model needed, so the match must use first AND last name."""
    low = context.lower()
    for m in _CAP_NAME.finditer(context):
        toks = m.group(1).split()
        # "Meet Priya Sharma": also try the sequence without a leading or trailing non-name word.
        for cand in (toks, toks[1:], toks[:-1]):
            name = _valid_name(" ".join(cand), low) if len(cand) >= 2 else None
            if name:
                person = Person(name=name)
                if match_full_name(local, person):
                    return person
    return None


def person_context(text: str, name: str) -> str:
    i = text.lower().find(name.lower())
    return _window(text, i, i + len(name)) if i >= 0 else ""


def match_person(local: str, people: list[Person]) -> Person | None:
    """Does this local part look like a listed person (first, first.last, flast, ...)?"""
    local = re.sub(r"[+].*$", "", local).replace("_", ".").replace("-", ".")
    for p in people:
        toks = [t for t in re.split(r"[\s\-]+", _ascii(p.name).replace(".", "")) if t]
        if len(toks) < 2:
            continue
        first, last = toks[0], toks[-1]
        forms = {first, last, f"{first}.{last}", f"{first}{last}", f"{first[0]}.{last}", f"{first[0]}{last}",
                 f"{first}.{last[0]}", f"{first}{last[0]}", f"{last}.{first}", f"{last}{first}", f"{last}.{first[0]}"}
        if local in forms:
            return p
    return None


def name_from_local(local: str) -> str:
    parts = [t for t in re.split(r"[._\-]", local.split("+")[0]) if t]
    if not parts or (len(parts) == 1 and len(parts[0]) < 3):
        return ""
    return " ".join(t.capitalize() for t in parts)


# ---- per-page assembly ----------------------------------------------------------------------

def extract_page(
    page: Page,
    site_domains: set[str],
    settings: AppSettings,
    *,
    people_fn: PeopleFn | None = None,
    domain: str = "",
    company: str = "",
) -> PageResult:
    """Emails (and, where useful, people) from one page, as lead drafts. Guesses are made site-wide.
    Plain-code heuristics run first; the model is asked only if they could not resolve who is who."""
    from backend.core import analysis  # imported here: analysis builds on this module

    a = analysis.analyze_page(page.url, page.html, site_domains, domain=domain, company=company)
    llm_people: list[Person] = []
    calls = 0
    if people_fn is not None and a.needs_llm:
        try:
            llm_people = find_people(a.llm_text, domain, people_fn)
            calls = 1
        except llm.LlmError as e:
            log.info("people extraction skipped for %s: %s", page.url, e)
    res = analysis.finalize_page(a, llm_people)
    res.llm_calls = calls
    return res


# ---- choosing the best few addresses of a site --------------------------------------------------

_PREFERRED: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # mode -> (best local-part prefixes, still useful)
    "internship": (("career", "job", "hr", "hiring", "recruit", "talent", "intern", "join", "people", "work"),
                   ("founder", "team", "hello", "hi", "contact", "info", "connect")),
    "investor": (("pitch", "found", "invest", "partner", "dealflow", "submission", "apply", "startup"),
                 ("contact", "info", "hello", "team", "connect", "enquir")),
    "service": (("hello", "hi", "contact", "info", "team", "business", "connect", "enquir", "inquir", "say"),
                ("sales", "partner", "studio", "mail", "office")),
}


def _score(d: LeadDraft, mode: str, target_roles: list[str]) -> int:
    local = domains.split_email(d.email)[0]
    if d.email_confidence == EmailConfidence.published_personal:
        return 100 + (10 if role_matches(d.role, target_roles) else 0)
    if d.email_confidence == EmailConfidence.guessed:
        return 30
    best, ok = _PREFERRED.get(mode, ((), ()))
    s = 80 if local.startswith(best) else 50 if local.startswith(ok) else 20
    if len(local) > 22 or sum(ch.isdigit() for ch in local) >= 2 or local.count("-") + local.count("_") >= 3:
        s -= 20  # machine-looking or country-office style ("ibm_customer_support_bulgaria", "6557")
    return s


def select_best(drafts: list[LeadDraft], mode: str, limit: int, target_roles: list[str] | None = None) -> list[LeadDraft]:
    """The `limit` most useful addresses of one site for this kind of outreach. A large company's contact
    page can list dozens of regional mailboxes; a site should contribute a handful, best first."""
    ranked = sorted(enumerate(drafts), key=lambda it: (-_score(it[1], mode, target_roles or []), it[0]))
    return [d for _, d in ranked[:limit]]


# ---- guessed addresses (site-wide, after all pages) ---------------------------------------------

_PATTERNS: dict[str, Callable[[str, str], str]] = {
    "first": lambda f, l: f,
    "first.last": lambda f, l: f"{f}.{l}",
    "flast": lambda f, l: f"{f[0]}{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "first.l": lambda f, l: f"{f}.{l[0]}",
}


def infer_pattern(drafts: list[LeadDraft]) -> str | None:
    """The address pattern this domain uses, judged from published personal addresses of named people."""
    counts: dict[str, int] = {}
    for d in drafts:
        if d.email_confidence != EmailConfidence.published_personal or not d.name or " " not in d.name:
            continue
        toks = [t for t in _ascii(d.name).replace(".", "").split() if t]
        local = domains.split_email(d.email)[0]
        for pname, fn in _PATTERNS.items():
            if toks and fn(toks[0], toks[-1]) == local:
                counts[pname] = counts.get(pname, 0) + 1
    return max(counts, key=counts.__getitem__) if counts else None


def role_matches(role: str, targets: list[str]) -> bool:
    low = role.lower()
    return bool(low) and any(t.lower() in low or low in t.lower() for t in targets if t)


def make_guesses(
    people: list[tuple[Person, str]],
    drafts: list[LeadDraft],
    domain: str,
    company: str,
    source_url_for: Callable[[Person], str],
    target_roles: list[str],
    limit: int = 3,
) -> list[LeadDraft]:
    """Guessed addresses for target people we found no published email for. Only MX-checkable."""
    have_names = {d.name.lower() for d in drafts if d.name}
    have_emails = {d.email for d in drafts}
    pattern = infer_pattern(drafts) or "first"
    out: list[LeadDraft] = []
    for person, ctx in people:
        if len(out) >= limit:
            break
        if person.name.lower() in have_names or not role_matches(person.role, target_roles):
            continue
        if not role_is_title_only(person.role) or not guessable_source(source_url_for(person)):
            continue  # a role naming another organisation, or a page that is not this site's own team page
        toks = [t for t in re.split(r"\s+", _ascii(person.name).replace(".", "").replace("'", "")) if t]
        if len(toks) < 2:
            continue
        email = f"{_PATTERNS[pattern](toks[0], toks[-1])}@{domain}"
        if email in have_emails or not domains.looks_like_email(email):
            continue
        out.append(LeadDraft(
            email=email, name=person.name, role=person.role, company=company, domain=domain,
            source_url=source_url_for(person), context_snippet=ctx,
            email_confidence=EmailConfidence.guessed, notes=f"Guessed ({pattern}@) from a listed person; not verified",
        ))
    return out
