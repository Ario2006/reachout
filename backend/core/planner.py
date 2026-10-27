"""Turn a plain-English outreach request into an editable search plan."""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field, field_validator

from backend.core import llm
from backend.core.models import AppSettings, Mode

log = logging.getLogger("reachout.planner")
MAX_QUERIES = 12


class Plan(BaseModel):
    mode: Mode
    target_roles: list[str] = Field(default_factory=list, max_length=12)
    industries: list[str] = Field(default_factory=list, max_length=12)
    regions: list[str] = Field(default_factory=list, max_length=12)
    keywords: list[str] = Field(default_factory=list, max_length=12)
    search_queries: list[str] = Field(default_factory=list, max_length=MAX_QUERIES)

    @field_validator("target_roles", "industries", "regions", "keywords", "search_queries")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            item = re.sub(r"\s+", " ", item).strip().strip('"')
            if item and item.lower() not in {o.lower() for o in out}:
                out.append(item[:120])
        return out


class PlanResult(BaseModel):
    plan: Plan
    source: str  # "llm" | "fallback"
    note: str = ""


# ---- mode detection and templates ---------------------------------------------------------

_MODE_HINTS: dict[Mode, tuple[str, ...]] = {
    Mode.internship: ("intern", "internship", "hiring", "job", "career", "placement", "opening", "fresher", "trainee"),
    Mode.investor: ("investor", "vc ", "venture", "angel", "seed fund", "pre-seed", "preseed", "fund", "back ", "backs", "backing", "syndicate", "capital"),
    Mode.service: ("need a", "needs a", "redesign", "agency", "freelanc", "services", "clients", "website", "branding", "marketing", "founders of", "owners of"),
}

_DEFAULT_ROLES: dict[Mode, list[str]] = {
    Mode.internship: ["founder", "CTO", "engineering manager", "recruiter", "head of engineering"],
    Mode.investor: ["partner", "associate", "principal", "general partner", "angel investor"],
    Mode.service: ["founder", "owner", "co-founder", "head of marketing", "CEO"],
}

_QUERY_TEMPLATES: dict[Mode, list[str]] = {
    # Company-site pages first (careers/contact/about), then list pages whose links lead to companies.
    Mode.internship: ["{ind} startup {reg} careers", "top {ind} startups in {reg}", "{ind} startup {reg} contact us",
                      "list of {ind} startups {reg}", "{ind} startups in {reg} about us team", "{ind} seed stage startup {reg} founders",
                      "{ind} startup {reg} hiring interns", "best {ind} startups to work for {reg}"],
    Mode.investor: ["{ind} venture capital firms {reg} contact", "list of {ind} VC funds {reg}", "{ind} pre-seed seed investors {reg} team",
                    "top {ind} investors in {reg}", "angel investors {ind} {reg} pitch", "{ind} accelerator {reg} apply"],
    Mode.service: ["top {ind} brands in {reg}", "list of {ind} brands {reg}", "{ind} brand {reg} contact us",
                   "{ind} brand {reg} about us founders", "emerging {ind} startups {reg}", "{ind} online store {reg} our story"],
}

_JOBBOARD_Q = re.compile(r"\b(jobs?|vacanc\w+|openings?|salary|walk-?in|resume|freshers?)\b", re.I)
_COMPANY_Q = re.compile(r"\b(startups?|compan(y|ies)|firms?|founders?|team|about|contact|careers?|brands?|funds?|investors?|vcs?|agenc\w+)\b", re.I)


# In service mode the user *sells* this: searching for it finds competitors, not customers.
_SERVICE_Q = re.compile(r"\b(redesign\w*|re-design|web\s*design\w*|website\s*design\w*|designers?|agency|agencies|freelanc\w*|"
                        r"developers?|consultants?|consulting|services?|seo|marketing\s+agency)\b", re.I)


def _company_oriented(q: str, mode: Mode | None = None) -> bool:
    """Drop queries that would mostly return job boards ('software engineer jobs Bangalore'), and in
    service mode those about the service itself ('website redesign D2C brands' finds design agencies)."""
    if mode == Mode.service and _SERVICE_Q.search(q):
        return False
    return not _JOBBOARD_Q.search(q) or bool(_COMPANY_Q.search(q))


def _grounded_regions(regions: list[str], request: str) -> list[str]:
    """Keep only regions the user actually mentioned. The model likes to add 'United States'."""
    low = request.lower()
    return [r for r in regions if any(tok and tok in low for tok in re.split(r"[\s/,&-]+", r.lower()) if len(tok) >= 2)]


def _clean_industries(industries: list[str], mode: Mode) -> list[str]:
    if mode != Mode.service:
        return industries
    cleaned = [re.sub(r"\b(websites?|sites?)\b", "", re.sub(_SERVICE_Q, "", i)).strip(" ,-/") for i in industries]
    return [c for c in cleaned if c]


_NOT_CONTACTS = re.compile(r"\b(interns?|internships?|students?|freshers?|candidates?|applicants?|job seekers?|me|myself)\b", re.I)


def _merge_roles(model_roles: list[str], mode: Mode) -> list[str]:
    """Roles of the people to contact. Drops the user's own role (the model sometimes answers 'intern')
    and keeps the mode's defaults so guessed addresses always have sensible targets."""
    roles = [r for r in model_roles if not _NOT_CONTACTS.search(r)]
    have = {r.lower() for r in roles}
    return (roles + [d for d in _DEFAULT_ROLES[mode] if d.lower() not in have])[:12]


# Second-tier phrasings, used only when a run needs to widen its search.
_MORE_TEMPLATES: dict[Mode, list[str]] = {
    Mode.internship: ["{ind} companies in {reg}", "{ind} startup {reg} email contact", "{ind} startups {reg} directory",
                      "{ind} product company {reg} team", "{ind} startup {reg} about the founders", "fast growing {ind} startups {reg}",
                      "{ind} scaleup {reg} careers page", "{ind} companies {reg} we are hiring"],
    Mode.investor: ["{ind} seed fund {reg} contact", "{ind} VC firm {reg} about team", "{ind} angel network {reg}",
                    "{ind} venture studio {reg}", "early stage {ind} investors {reg} portfolio", "{ind} micro VC {reg} partners"],
    Mode.service: ["{ind} startups {reg} founders", "{ind} brand {reg} instagram founder contact", "{ind} companies {reg} contact email",
                   "indian {ind} brands {reg} about", "{ind} store {reg} contact us", "new {ind} brands {reg} launch"],
}


def more_queries(plan: Plan, used: set[str], n: int = 6) -> list[str]:
    """Fresh queries for a widening round: unused template variations over every industry and region."""
    inds = plan.industries or ["startup"]
    regs = plan.regions or [""]
    seen = {u.lower() for u in used}
    out: list[str] = []
    for tpl in _QUERY_TEMPLATES[plan.mode] + _MORE_TEMPLATES[plan.mode]:
        for ind in inds:
            for reg in regs:
                q = re.sub(r"\s+", " ", tpl.format(ind=ind, reg=reg)).strip()
                if q.lower() not in seen:
                    seen.add(q.lower())
                    out.append(q)
                    if len(out) >= n:
                        return out
    return out


def detect_mode(text: str) -> Mode:
    low = " " + text.lower() + " "
    scores = {m: sum(1 for h in hints if h in low) for m, hints in _MODE_HINTS.items()}
    best = max(scores.values())
    if best == 0:
        return Mode.service
    return next(m for m in (Mode.internship, Mode.investor, Mode.service) if scores[m] == best)


_REGIONS = ("bangalore", "bengaluru", "mumbai", "delhi", "new delhi", "gurgaon", "gurugram", "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad", "kochi", "jaipur", "india", "sea", "southeast asia", "singapore", "indonesia", "vietnam", "malaysia", "thailand", "philippines", "usa", "us", "united states", "uk", "london", "europe", "berlin", "dubai", "uae", "san francisco", "new york", "canada", "toronto", "australia", "remote")
_NOISE = {"in", "at", "for", "of", "the", "a", "an", "and", "or", "to", "who", "that", "need", "needs", "with", "i", "want", "find", "me", "my", "looking", "openings", "opening", "back", "backs", "backing", "internship", "internships", "intern", "interns", "investor", "investors", "founder", "founders", "owner", "owners", "startups", "startup", "companies", "company", "brands", "brand", "website", "redesign", "seed-stage", "pre-seed", "preseed"}


def _fallback_plan(request: str, mode: Mode) -> Plan:
    """Deterministic plan from the request text when the model is unavailable or unusable."""
    low = request.lower()
    regions = [r.title() if len(r) > 3 else r.upper() for r in _REGIONS if re.search(rf"\b{re.escape(r)}\b", low)]
    stripped = low
    for r in _REGIONS:
        stripped = re.sub(rf"\b{re.escape(r)}\b", " ", stripped)
    words = [w for w in re.findall(r"[a-z][a-z0-9/+\-]*", stripped) if w not in _NOISE]
    industries = [" ".join(words[:3])] if words else ["technology"]
    stage = [k for k in ("seed", "pre-seed", "early stage", "series a") if k in low]
    plan = Plan(mode=mode, target_roles=_DEFAULT_ROLES[mode], industries=industries, regions=regions[:3], keywords=stage)
    plan.search_queries = _expand_queries(plan)
    return plan


def _expand_queries(plan: Plan, keep_model_queries: int = 4) -> list[str]:
    """A few of the model's company-oriented queries, then template queries (varying industry and
    region), up to MAX_QUERIES. Templates guarantee queries that surface company sites and list pages."""
    inds = plan.industries or ["startup"]
    regs = plan.regions or [""]
    queries = [q for q in plan.search_queries if _company_oriented(q, plan.mode)][:keep_model_queries]
    seen = {q.lower() for q in queries}
    combos = [(i, r) for i in inds[:2] for r in regs[:2]]
    for n, tpl in enumerate(_QUERY_TEMPLATES[plan.mode]):
        ind, reg = combos[n % len(combos)]
        q = re.sub(r"\s+", " ", tpl.format(ind=ind, reg=reg)).strip()
        if q.lower() not in seen:
            seen.add(q.lower())
            queries.append(q)
    return queries[:MAX_QUERIES]


_SYSTEM = """You convert a user's outreach request into a web-search plan. Output JSON only.
Fields:
- mode: "internship" (user wants internships/jobs at companies), "investor" (user wants to reach investors/VCs/angels), or "service" (user sells a service to businesses).
- target_roles: job titles of the people to contact (e.g. founder, CTO, partner, head of marketing).
- industries: the sectors or niches of the target organisations.
- regions: cities, countries or regions to focus on.
- keywords: other useful qualifiers (stage, size, topics).
- search_queries: 6 to 10 different web-search queries that would surface the target organisations' OWN websites or lists of such organisations (for example "AI startup Bangalore careers", "top fintech startups in India"). Always name the kind of organisation (startup, company, brand, VC firm). Never write queries about job listings or job boards. Each query is 3 to 8 words. No quotes, no search operators."""


def make_plan(request: str, settings: AppSettings, mode: Mode | None = None) -> PlanResult:
    request = request.strip()
    if not request:
        raise ValueError("Request is empty")
    hint = f"\nThe user chose mode: {mode.value}." if mode else ""
    try:
        plan = llm.generate_json(
            Plan, _SYSTEM, f"Request: {request}{hint}",
            model=settings.ollama_model, num_ctx=settings.num_ctx, keep_alive=settings.keep_alive,
            timeout_s=60,
        )
        if mode:
            plan.mode = mode  # an explicit user choice always wins
        source, note = "llm", ""
        plan.regions = _grounded_regions(plan.regions, request)
        plan.industries = _clean_industries(plan.industries, plan.mode) or plan.industries
        plan.search_queries = _expand_queries(plan)  # model queries (filtered) + guaranteed template queries
        plan.target_roles = _merge_roles(plan.target_roles, plan.mode)
        return PlanResult(plan=plan, source=source, note=note)
    except llm.LlmError as e:
        log.warning("planner fell back to templates: %s", e)
        m = mode or detect_mode(request)
        reason = ("The language model is not running" if re.search(r"connect|refused|unreachable|not running", str(e), re.I)
                  else "The language model did not return a usable plan")
        return PlanResult(plan=_fallback_plan(request, m), source="fallback",
                          note=f"{reason}, so this plan is a template built from your wording. Please review it.")
