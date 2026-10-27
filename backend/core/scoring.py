"""Lead score, 0 to 100, with the reasons that add up to it. Pure function: same inputs, same score.

The weights favour what predicts a delivered, relevant email: a published personal address on a team page,
next to a name and a role that match who you are looking for, at a domain with a mail server."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from backend.core import domains
from backend.core.extractor import LeadDraft, role_matches
from backend.core.models import EmailConfidence

CONFIDENCE_POINTS = {EmailConfidence.published_personal: 40, EmailConfidence.published_generic: 22, EmailConfidence.guessed: 8}
PAGE_POINTS = {"team": 10, "contact": 8, "osm": 6, "about": 5, "careers": 4, "home": 3}
JOB_MODES = {"internship"}  # a careers page is where these modes' contacts live
DAY = 86_400.0


@dataclass(frozen=True)
class Reason:
    label: str
    points: int
    detail: str

    def as_dict(self) -> dict:
        return {"label": self.label, "points": self.points, "detail": self.detail}


def _name_matches_local(name: str, local: str) -> bool:
    toks = [t for t in re.split(r"[^a-z]+", name.lower()) if len(t) > 1]
    local = local.lower()
    return bool(toks) and any(t in local for t in toks)


def score_lead(d: LeadDraft, *, mode: str | None, target_roles: list[str] | None = None, mx_ok: bool | None = None,
               now: float | None = None) -> tuple[int, list[Reason]]:
    now = time.time() if now is None else now
    reasons: list[Reason] = []
    label = {EmailConfidence.published_personal: "a person's address published on the site",
             EmailConfidence.published_generic: "a role address published on the site",
             EmailConfidence.guessed: "built from a listed name and the domain's address pattern"}[d.email_confidence]
    reasons.append(Reason("confidence", CONFIDENCE_POINTS[d.email_confidence], label.capitalize()))

    if d.name:
        if _name_matches_local(d.name, domains.split_email(d.email)[0]):
            reasons.append(Reason("name", 15, f"The address matches the name {d.name}"))
        else:
            reasons.append(Reason("name", 5, f"A name ({d.name}) is on the page, but it does not match the address"))
    if d.role:
        if target_roles and role_matches(d.role, target_roles):
            reasons.append(Reason("role", 15, f"{d.role} is one of the roles you asked for"))
        else:
            reasons.append(Reason("role", 6, f"Has a job title ({d.role}), not one you asked for"))

    page = d.page_type or ""
    pts = PAGE_POINTS.get(page, 0)
    if page == "careers" and mode in JOB_MODES:
        pts = 8
    if pts:
        reasons.append(Reason("page", pts, f"Found on a {page} page"))

    mx = True if mx_ok is None else mx_ok  # callers that score drafts have already dropped no-MX domains
    if mx:
        reasons.append(Reason("mx", 10, f"{domains.split_email(d.email)[1]} has a mail server"))
    if d.fetched_at:
        age = now - d.fetched_at
        if age <= DAY:
            reasons.append(Reason("fresh", 5, "Page was fetched in the last day"))
        elif age <= 14 * DAY:
            reasons.append(Reason("fresh", 3, "Page was fetched in the last two weeks"))
    return max(0, min(100, sum(r.points for r in reasons))), reasons
