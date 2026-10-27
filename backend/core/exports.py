"""CSV export and import of leads."""
from __future__ import annotations

import csv
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import Engine

from backend.core import db, domains, leads, validator
from backend.core.extractor import LeadDraft
from backend.core.models import EmailConfidence, Mode

COLUMNS = ["name", "email", "role", "company", "domain", "source_url", "email_confidence", "mode", "notes"]
MAX_IMPORT_ROWS = 20_000
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: object) -> str:
    """Neutralise spreadsheet formula injection: a leading = + - @ makes Excel/Numbers run it."""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_FORMULA_PREFIXES) else s


def export_csv(f: leads.LeadFilter, sort: str = "", engine: Engine | None = None) -> str:
    engine = engine or db.get_engine()
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLUMNS)
    offset = 0
    while True:
        rows, _ = leads.query_leads(f, sort, offset, 1000, engine)
        if not rows:
            break
        for r in rows:
            w.writerow([_safe(r.get(c)) for c in COLUMNS])
        offset += len(rows)
    return out.getvalue()


@dataclass
class RowIssue:
    row: int
    email: str
    reason: str


@dataclass
class ImportReport:
    total: int = 0
    valid: int = 0
    inserted: int = 0
    duplicates: int = 0
    suppressed: int = 0
    invalid: int = 0
    no_mx: int = 0
    issues: list[RowIssue] = field(default_factory=list)
    drafts: list[tuple[LeadDraft, str | None]] = field(default_factory=list)  # internal


def _http_url(v: str) -> str:
    """Only http(s) URLs are kept as a lead's source; anything else (n/a, javascript:, file:) is dropped."""
    v = v.strip()
    return v if v.lower().startswith(("http://", "https://")) and len(v) <= 2000 else ""


def _clean_cell(v: str | None) -> str:
    return (v or "").strip()


def import_csv(text: str, *, dry_run: bool = False, engine: Engine | None = None,
               mx: validator.MxChecker | None = None) -> ImportReport:
    """Validate (syntax + MX + suppression + duplicates) and, unless dry_run, insert."""
    engine = engine or db.get_engine()
    mx = mx or validator.MxChecker()
    rep = ImportReport()
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise ValueError("The file is empty")
    fields = {(h or "").strip().lower(): h for h in reader.fieldnames}
    if "email" not in fields:
        raise ValueError("The CSV needs an 'email' column. Expected columns: " + ", ".join(COLUMNS))

    parsed: list[tuple[int, dict[str, str]]] = []
    for i, raw in enumerate(reader, start=2):  # row 1 is the header
        if i - 1 > MAX_IMPORT_ROWS:
            raise ValueError(f"Too many rows (limit {MAX_IMPORT_ROWS})")
        parsed.append((i, {k: _clean_cell(raw.get(fields[k])) for k in fields}))
    rep.total = len(parsed)

    seen: set[str] = set()
    candidates: list[tuple[int, str, dict[str, str]]] = []
    for row, data in parsed:
        email = validator.normalize_syntax(data.get("email", ""))
        if email is None or domains.is_junk_email(email):
            rep.invalid += 1
            rep.issues.append(RowIssue(row, data.get("email", ""), "Invalid or unusable email address"))
        elif email in seen:
            rep.duplicates += 1
            rep.issues.append(RowIssue(row, email, "Duplicate within the file"))
        else:
            seen.add(email)
            candidates.append((row, email, data))

    uniq = sorted({domains.split_email(e)[1] for _, e, _ in candidates})
    with ThreadPoolExecutor(max_workers=8) as pool:
        mx_ok = dict(zip(uniq, pool.map(mx.has_mx, uniq)))

    for row, email, data in candidates:
        if mx_ok.get(domains.split_email(email)[1]) is not True:
            rep.no_mx += 1
            rep.issues.append(RowIssue(row, email, "Domain has no mail server (MX record)"))
            continue
        if db.is_suppressed(email, engine):
            rep.suppressed += 1
            rep.issues.append(RowIssue(row, email, "On the suppression list"))
            continue
        try:
            conf = EmailConfidence(data["email_confidence"]) if data.get("email_confidence") else validator.classify_confidence(email)
        except ValueError:
            conf = validator.classify_confidence(email)
        mode = data.get("mode") if data.get("mode") in {m.value for m in Mode} else None
        rep.drafts.append((LeadDraft(
            email=email, name=data.get("name", ""), role=data.get("role", ""), company=data.get("company", ""),
            domain=data.get("domain") or domains.registrable(domains.split_email(email)[1]),
            source_url=_http_url(data.get("source_url", "")), email_confidence=conf, notes=data.get("notes", ""),
        ), mode))
        rep.valid += 1

    if not dry_run:
        by_mode: dict[str | None, list[LeadDraft]] = {}
        for d, m in rep.drafts:
            by_mode.setdefault(m, []).append(d)
        for m, drafts in by_mode.items():
            res = leads.ingest(drafts, run_id=None, mode=m, engine=engine)
            rep.inserted += len(res.new)
            rep.duplicates += res.duplicates
    rep.issues = rep.issues[:100]
    return rep
