"""Find people (name, role, email) with plain code: JSON-LD, schema.org microdata, and team-card HTML.
Cheap and deterministic, so the language model is only needed for what these cannot read."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser

from backend.core import domains
from backend.core.extractor import _valid_name, role_is_title_only

_TITLE_HINT = re.compile(
    r"\b(founder|co-?founder|ceo|cto|coo|cfo|cmo|cpo|chief|president|vice president|vp|head of|director|partner|principal|"
    r"associate|manager|lead|engineer|developer|designer|analyst|recruiter|talent|advisor|chairman|chair|owner|investor|"
    r"scientist|officer|executive|consultant|specialist)\b", re.I)
_PERSON_TYPES = {"person"}
_ORG_TYPES = {"organization", "localbusiness", "corporation", "ngo", "educationalorganization", "store", "professionalservice"}
MAX_CARD_NODES = 4000


@dataclass
class StructuredPerson:
    name: str
    role: str = ""
    email: str = ""
    source: str = ""  # "jsonld" | "microdata" | "card" | "heading"


@dataclass
class StructuredFindings:
    people: list[StructuredPerson] = field(default_factory=list)
    org_name: str = ""
    org_emails: list[str] = field(default_factory=list)

    def by_email(self) -> dict[str, StructuredPerson]:
        return {p.email: p for p in self.people if p.email}


def looks_like_title(s: str) -> bool:
    s = s.strip()
    return 2 <= len(s) <= 70 and (role_is_title_only(s) or bool(_TITLE_HINT.search(s)))


def _clean_name(raw: str) -> str | None:
    raw = re.sub(r"\s+", " ", raw).strip(" ,.;:-–—|•")
    return _valid_name(raw, raw.lower()) if raw else None


def _clean_email(raw: str) -> str:
    e = raw.strip().lower().removeprefix("mailto:").split("?")[0].strip()
    return e if domains.looks_like_email(e) and not domains.is_junk_email(e) else ""


def _listify(v: object) -> list:
    return v if isinstance(v, list) else [] if v is None else [v]


def _types(node: dict) -> set[str]:
    return {str(t).lower().rsplit("/", 1)[-1] for t in _listify(node.get("@type"))}


def _walk_jsonld(node: object, out: StructuredFindings, depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(node, list):
        for n in node:
            _walk_jsonld(n, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    types = _types(node)
    if "@graph" in node:
        _walk_jsonld(node["@graph"], out, depth + 1)
    if types & _PERSON_TYPES:
        name = _clean_name(str(node.get("name", "")))
        if name:
            role = node.get("jobTitle") or node.get("roleName") or ""
            role = ", ".join(map(str, role)) if isinstance(role, list) else str(role)
            out.people.append(StructuredPerson(name, re.sub(r"\s+", " ", role).strip()[:80], _clean_email(str(node.get("email", ""))), "jsonld"))
    if types & _ORG_TYPES:
        if not out.org_name and isinstance(node.get("name"), str):
            out.org_name = re.sub(r"\s+", " ", node["name"]).strip()[:80]
        for e in [node.get("email")] + [cp.get("email") for cp in _listify(node.get("contactPoint")) if isinstance(cp, dict)]:
            em = _clean_email(str(e or ""))
            if em and em not in out.org_emails:
                out.org_emails.append(em)
        for key, role in (("founder", "Founder"), ("founders", "Founder"), ("employee", ""), ("employees", ""), ("member", ""), ("member", "")):
            for child in _listify(node.get(key)):
                if isinstance(child, dict):
                    c = dict(child)
                    c.setdefault("@type", "Person")
                    if role and not c.get("jobTitle"):
                        c["jobTitle"] = role
                    _walk_jsonld(c, out, depth + 1)


def jsonld_findings(tree: LexborHTMLParser, out: StructuredFindings) -> None:
    for s in tree.css('script[type="application/ld+json"]'):
        raw = (s.text() or "").strip()
        if not raw or len(raw) > 500_000:
            continue
        try:
            _walk_jsonld(json.loads(raw), out)
        except (ValueError, RecursionError):
            continue


def microdata_findings(tree: LexborHTMLParser, out: StructuredFindings) -> None:
    for el in tree.css('[itemtype*="schema.org/Person"]'):
        def prop(name: str) -> str:
            n = el.css_first(f'[itemprop="{name}"]')
            if n is None:
                return ""
            return n.attributes.get("content") or n.attributes.get("href") or n.text(strip=True)

        name = _clean_name(prop("name"))
        if name:
            out.people.append(StructuredPerson(name, prop("jobTitle")[:80], _clean_email(prop("email")), "microdata"))


def _lines(node) -> list[str]:  # noqa: ANN001
    return [ln.strip() for ln in re.split(r"[\n\r]+", node.text(separator="\n")) if ln.strip()]


def _card_person(card) -> StructuredPerson | None:  # noqa: ANN001
    lines = _lines(card)[:8]
    name, role = None, ""
    for i, ln in enumerate(lines):
        if name is None:
            n = _clean_name(ln)
            if n:
                name = n
                continue
        elif looks_like_title(ln) and not _clean_name(ln):
            role = ln[:80]
            break
        elif i > 3:
            break
    if not name:
        return None
    mail = card.css_first('a[href^="mailto:"]')
    return StructuredPerson(name, role, _clean_email(mail.attributes.get("href", "")) if mail else "", "card")


def card_findings(tree: LexborHTMLParser, out: StructuredFindings) -> None:
    """Repeated sibling blocks that each hold a name and a job title (the usual "team grid")."""
    body = tree.body
    if body is None:
        return
    seen = 0
    best: list[StructuredPerson] = []
    for parent in body.css("*"):
        seen += 1
        if seen > MAX_CARD_NODES:
            break
        kids = [k for k in parent.iter(include_text=False)]
        if len(kids) < 3:
            continue
        sig: dict[str, list] = {}
        for k in kids:
            sig.setdefault(f"{k.tag}.{' '.join(sorted((k.attributes.get('class') or '').split()))}", []).append(k)
        for group in sig.values():
            if len(group) < 3:
                continue
            people = [p for p in (_card_person(c) for c in group[:60]) if p and p.role]
            if len(people) >= 3 and len(people) >= len(group) / 2 and len(people) > len(best):
                best = people
    out.people.extend(best)
    if not best:  # headings followed by a title line: <h3>Name</h3><p>CEO</p>
        pairs = []
        for h in body.css("h1,h2,h3,h4,h5,h6,strong,b"):
            name = _clean_name(h.text(strip=True))
            nxt = h.next
            while nxt is not None and not (nxt.tag and nxt.tag not in ("-text", "#text")):
                nxt = nxt.next
            if name and nxt is not None and looks_like_title(nxt.text(strip=True)):
                pairs.append(StructuredPerson(name, nxt.text(strip=True)[:80], "", "heading"))
        if len(pairs) >= 2:
            out.people.extend(pairs)


def structured_findings(html: str) -> StructuredFindings:
    """Everything findable without a model. Malformed markup yields fewer findings, never an error."""
    out = StructuredFindings()
    try:
        tree = LexborHTMLParser(html)
        jsonld_findings(tree, out)
        microdata_findings(tree, out)
        if not out.people:  # cards are the fallback; structured data, when present, is authoritative
            tree2 = LexborHTMLParser(html)
            for n in tree2.css("script,style,noscript,svg,template"):
                n.decompose()
            card_findings(tree2, out)
    except Exception:  # noqa: BLE001 - hostile or broken HTML must never fail a crawl job
        return out
    seen: set[str] = set()
    uniq = []
    for p in out.people:
        if p.name.lower() not in seen:
            seen.add(p.name.lower())
            uniq.append(p)
    out.people = uniq
    return out
