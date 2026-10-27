"""Staged page analysis. `analyze_page` does everything that needs no model (emails, structured data,
team cards, names beside addresses) and decides whether the model is needed. `finalize_page` turns that
plus any model-supplied people into lead drafts, recording why each lead was labelled the way it was."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.core import crawlplan, domains
from backend.core.extractor import (
    PEOPLE_PROMPT_CHARS, EmailHit, LeadDraft, PageResult, Person, company_name, extract_emails, jobish, main_text,
    match_person, people_near_email, person_context, visible_text, _OTHERS_PATH, _ROLE_WORDS,
)
from backend.core.heuristics import StructuredFindings, structured_findings
from backend.core.models import EmailConfidence
from urllib.parse import urlsplit


@dataclass
class PageAnalysis:
    url: str
    domain: str
    company: str
    text: str
    hits: list[EmailHit]
    page_type: str = "other"
    tier: str = "http"
    fetched_at: float = 0.0
    structured: StructuredFindings = field(default_factory=StructuredFindings)
    needs_llm: bool = False
    llm_text: str = ""
    unresolved: list[str] = field(default_factory=list)  # personal-looking addresses nobody has named yet


# A page that lists people. /about and /contact only count when the text itself is full of job titles.
_STRICT_TEAM_PATH = re.compile(r"/(team|people|leadership|management|founders?|investors?|partners?|our-team|who-we-are)(/|$|-)", re.I)


def team_block(url: str, text: str) -> bool:
    return bool(_STRICT_TEAM_PATH.search(urlsplit(url).path + "/")) or len(_ROLE_WORDS.findall(text)) >= 3


def _personal_looking(h: EmailHit) -> bool:
    return domains.namelike_local(domains.split_email(h.email)[0])


def analyze_page(url: str, html: str, site_domains: set[str], *, domain: str = "", company: str = "",
                 tier: str = "http", page_type: str = "", fetched_at: float = 0.0) -> PageAnalysis:
    page_type = page_type or crawlplan.classify(url)
    if not html:
        return PageAnalysis(url, domain, company, "", [], page_type, tier, fetched_at)
    structured = structured_findings(html)
    hits = [h for h in extract_emails(html)
            if not domains.is_junk_email(h.email) and domains.registrable(domains.split_email(h.email)[1]) in site_domains]
    have = {h.email for h in hits}
    for em in structured.org_emails:  # a contact address declared in the page's structured data
        if em not in have and domains.registrable(domains.split_email(em)[1]) in site_domains:
            hits.append(EmailHit(email=em, context="Listed in the page's structured data", via="jsonld"))
    have = {h.email for h in hits}
    for sp in structured.people:  # an address declared for a person in structured data
        if sp.email and sp.email not in have and domains.registrable(domains.split_email(sp.email)[1]) in site_domains:
            hits.append(EmailHit(email=sp.email, context=f"{sp.name}, {sp.role}".strip(", "), via=sp.source))
            have.add(sp.email)
    text = visible_text(html)
    company = company or structured.org_name or company_name(html, domain or next(iter(site_domains), ""))
    a = PageAnalysis(url, domain, company, text, hits, page_type, tier, fetched_at, structured)

    # Who is already identified without a model?
    by_email = structured.by_email()
    people = [Person(name=p.name, role=p.role) for p in structured.people]
    for h in hits:
        local = domains.split_email(h.email)[0]
        if _personal_looking(h) and h.email not in by_email and match_person(local, people) is None \
                and people_near_email(h.context, local) is None:
            a.unresolved.append(h.email)
    others = bool(_OTHERS_PATH.search(urlsplit(url).path + "/"))
    team_unresolved = team_block(url, text) and not structured.people
    a.needs_llm = bool(text) and not jobish(url) and not others and (bool(a.unresolved) or team_unresolved)
    if a.needs_llm:
        a.llm_text = main_text(html, PEOPLE_PROMPT_CHARS)
    return a


def finalize_page(a: PageAnalysis, llm_people: list[Person] | None = None) -> PageResult:
    res = PageResult(emails_found=len(a.hits), needs_llm=a.needs_llm)
    llm_people = llm_people or []
    structured_people = [Person(name=p.name, role=p.role) for p in a.structured.people]
    people = structured_people + [p for p in llm_people if p.name.lower() not in {s.name.lower() for s in structured_people}]
    how: dict[str, str] = {p.name.lower(): "structured data or a team card" for p in structured_people}
    how.update({p.name.lower(): "the language model" for p in llm_people if p.name.lower() not in how})
    by_email = a.structured.by_email()
    res.people = []
    seen_people: set[str] = set()

    def add_person(p: Person) -> None:
        if p.name.lower() not in seen_people:
            seen_people.add(p.name.lower())
            res.people.append((p, person_context(a.text, p.name)))

    for p in people:
        add_person(p)

    for h in a.hits:
        local, edomain = domains.split_email(h.email)
        generic = domains.is_generic_local(local)
        sp = by_email.get(h.email)
        person: Person | None = Person(name=sp.name, role=sp.role) if sp else None
        found_by = "an email declared next to the person in structured data or a team card" if sp else ""
        if person is None:
            person = match_person(local, people)
            if person:
                found_by = f"matches a person listed by {how.get(person.name.lower(), 'the page')}"
        if person is None and not generic and domains.namelike_local(local):
            person = people_near_email(h.context, local)
            if person is not None:
                found_by = "the full name appears beside the address on the page"
                how[person.name.lower()] = found_by
                people.append(person)
                add_person(person)
        trace: list[tuple[str, str]] = [("found", f"Found {h.email} on {a.url} ({h.via}; page type {a.page_type}; served from {a.tier})")]
        note = ""
        if person:
            conf, name, role = EmailConfidence.published_personal, person.name, person.role
            ctx = person_context(a.text, person.name) or h.context
            trace.append(("label", f"Labelled personal: the name '{person.name}' {found_by}"))
        else:
            conf, name, role, ctx = EmailConfidence.published_generic, "", "", h.context
            if not generic and domains.namelike_local(local):
                note = "Looks like a personal address, but no matching name was found on the page"
                trace.append(("label", "Labelled generic: it looks personal, but no name on the page matches it"))
            else:
                trace.append(("label", "Labelled generic: a role-style address (info@, careers@ ...)"))
        res.drafts.append(LeadDraft(
            email=h.email, name=name, role=role, company=a.company, domain=domains.registrable(edomain),
            source_url=a.url, context_snippet=ctx, email_confidence=conf, notes=note,
            page_tier=a.tier, page_type=a.page_type, fetched_at=a.fetched_at, trace=trace,
        ))
    return res
