"""Lead repository: ingest from the pipeline, query, edit, bulk actions."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Engine, bindparam, text

from backend.core import db, domains, events, scoring, slog
from backend.core.extractor import LeadDraft
from backend.core.models import EmailConfidence, LeadStatus, Mode

SORTABLE = {"score", "name", "email", "role", "company", "email_confidence", "status", "mode", "created_at",
            "updated_at", "domain"}
LIST_COLS = ("id, name, email, role, company, domain, source_url, email_confidence, mode, status, "
             "notes, created_at, updated_at, search_run_id, score")
EDITABLE = {"name", "role", "company", "notes", "status"}
_CONF_RANK = {"published_personal": 3, "published_generic": 2, "guessed": 1}


class LeadFilter(BaseModel):
    q: str = ""
    status: list[LeadStatus] = Field(default_factory=list)
    mode: list[Mode] = Field(default_factory=list)
    confidence: list[EmailConfidence] = Field(default_factory=list)
    run_id: int | None = None
    min_score: int | None = None


@dataclass
class IngestResult:
    new: list[dict] = field(default_factory=list)
    duplicates: int = 0
    suppressed: int = 0
    upgraded: int = 0


def _where(f: LeadFilter) -> tuple[str, dict, list]:
    clauses, params, expanding = [], {}, []
    if f.q.strip():
        clauses.append("(name LIKE :q ESCAPE '\\' OR email LIKE :q ESCAPE '\\' OR company LIKE :q ESCAPE '\\' "
                       "OR role LIKE :q ESCAPE '\\' OR domain LIKE :q ESCAPE '\\')")
        like = f.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["q"] = f"%{like}%"
    for col, vals in (("status", f.status), ("mode", f.mode), ("email_confidence", f.confidence)):
        if vals:
            key = f"{col}_vals"
            clauses.append(f"{col} IN :{key}")
            params[key] = [v.value for v in vals]
            expanding.append(key)
    if f.run_id is not None:
        clauses.append("search_run_id = :run_id")
        params["run_id"] = f.run_id
    if f.min_score is not None:
        clauses.append("score >= :min_score")
        params["min_score"] = f.min_score
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params, expanding


def _bind(sql: str, params: dict, expanding: list):
    stmt = text(sql)
    if expanding:
        stmt = stmt.bindparams(*[bindparam(k, expanding=True) for k in expanding])
    return stmt, params


def parse_sort(sort: str) -> str:
    """'company:asc,name:desc' -> a safe ORDER BY clause (whitelisted columns only)."""
    parts = []
    for item in [s for s in sort.split(",") if s.strip()]:
        col, _, direction = item.strip().partition(":")
        if col in SORTABLE:
            parts.append(f"{col} {'DESC' if direction.lower() == 'desc' else 'ASC'}")
    return ", ".join(parts) or "id DESC"


def query_leads(f: LeadFilter, sort: str = "", offset: int = 0, limit: int = 100,
                engine: Engine | None = None) -> tuple[list[dict], int]:
    engine = engine or db.get_engine()
    where, params, expanding = _where(f)
    order = parse_sort(sort)
    with engine.connect() as c:
        stmt, p = _bind(f"SELECT COUNT(*) FROM leads{where}", dict(params), expanding)
        total = int(c.execute(stmt, p).scalar_one())
        stmt, p = _bind(f"SELECT {LIST_COLS} FROM leads{where} ORDER BY {order}, id DESC LIMIT :lim OFFSET :off",
                        {**params, "lim": limit, "off": max(offset, 0)}, expanding)
        rows = [dict(r) for r in c.execute(stmt, p).mappings()]
    return rows, total


def get_lead(lead_id: int, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    with engine.connect() as c:
        row = c.execute(text(f"SELECT {LIST_COLS}, context_snippet FROM leads WHERE id = :i"), {"i": lead_id}).mappings().first()
        if row is None:
            return None
        lead = dict(row)
        extra = c.execute(text("SELECT score_reasons_json, trace_id FROM leads WHERE id = :i"), {"i": lead_id}).first()
        lead["score_reasons"] = json.loads(extra[0]) if extra and extra[0] else []
        lead["sources"] = [dict(r) for r in c.execute(text(
            "SELECT source_url, page_tier, page_type, fetched_at, context_snippet, first_seen, run_id FROM lead_sources "
            "WHERE lead_id = :i ORDER BY id"), {"i": lead_id}).mappings()]
        lead["trace"] = [dict(r) for r in c.execute(text(
            "SELECT at, kind, message FROM trace_events WHERE trace_id = :t ORDER BY id"),
            {"t": extra[1] if extra and extra[1] else "-"}).mappings()]
        run = None
        if lead["search_run_id"]:
            r = c.execute(text("SELECT id, request_text, started_at FROM search_runs WHERE id = :i"),
                          {"i": lead["search_run_id"]}).mappings().first()
            run = dict(r) if r else None
        timeline = [dict(r) for r in c.execute(text(
            "SELECT 'sent' AS type, kind AS detail, sent_at AS at FROM messages WHERE lead_id = :i AND sent_at IS NOT NULL "
            "UNION ALL SELECT type, snippet, occurred_at FROM events WHERE lead_id = :i ORDER BY at"), {"i": lead_id}).mappings()]
    lead["run"] = run
    lead["timeline"] = timeline
    return lead


def update_lead(lead_id: int, fields: dict, engine: Engine | None = None) -> dict | None:
    engine = engine or db.get_engine()
    clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items() if k in EDITABLE}
    if "status" in clean:
        clean["status"] = LeadStatus(clean["status"]).value
    if clean:
        sets = ", ".join(f"{k} = :{k}" for k in clean)
        with engine.begin() as c:
            c.execute(text(f"UPDATE leads SET {sets}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = :id"),
                      {**clean, "id": lead_id})
        events.emit("lead.updated", {"id": lead_id}, engine)
    return get_lead(lead_id, engine)


_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


def person_key(name: str, domain: str) -> str | None:
    """Identity of a person at a company: their name (at least two parts) and the registrable domain."""
    toks = [x for x in re.split(r"[^a-z]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()) if x]
    if len(toks) < 2 or not domain:
        return None
    return f"{toks[0]} {toks[-1]}@{domains.registrable(domain)}"


def _add_source(c, lead_id: int, run_id: int | None, url: str, tier: str, ptype: str, fetched_at: float, snippet: str) -> None:
    if url:
        c.execute(text("INSERT OR IGNORE INTO lead_sources (lead_id, run_id, source_url, page_tier, page_type, fetched_at, context_snippet) "
                       "VALUES (:l, :r, :u, :t, :p, :f, :x)"),
                  {"l": lead_id, "r": run_id, "u": url, "t": tier or None, "p": ptype or None, "f": fetched_at or None, "x": snippet or None})


def _add_trace(c, trace_id: str, run_id: int | None, entries: list[tuple[str, str]]) -> None:
    for kind, message in entries:
        c.execute(text("INSERT INTO trace_events (trace_id, run_id, kind, message) VALUES (:t, :r, :k, :m)"),
                  {"t": trace_id, "r": run_id, "k": kind, "m": message})


def _sources_of(d: LeadDraft) -> list[tuple[str, str, str, float, str]]:
    out = [(d.source_url, d.page_tier, d.page_type, d.fetched_at, d.context_snippet)]
    out += [(a.get("url", ""), a.get("tier", ""), a.get("type", ""), a.get("fetched_at", 0.0), a.get("snippet", "")) for a in d.also]
    return out


def ingest(drafts: list[LeadDraft], *, run_id: int | None, mode: str | None, target_roles: list[str] | None = None,
           engine: Engine | None = None) -> IngestResult:
    """Insert new leads, scored and traced. The same address, or the same person (name + company domain),
    is never stored twice: a better address upgrades the stored one and every source page is kept.
    Suppressed addresses are skipped. Emits a `lead` event per new lead."""
    engine = engine or db.get_engine()
    res = IngestResult()
    for d in drafts:
        email = d.email.strip().lower()
        if db.is_suppressed(email, engine):
            res.suppressed += 1
            continue
        score, reasons = scoring.score_lead(d, mode=mode, target_roles=target_roles, mx_ok=d.mx_ok)
        reasons_json = json.dumps([r.as_dict() for r in reasons])
        pkey = person_key(d.name, d.domain) if d.name else None
        tid = d.trace_id or slog.new_trace_id()
        rank = _CONF_RANK[d.email_confidence.value]
        with engine.begin() as c:
            existing = c.execute(text("SELECT id, email, email_confidence, score, trace_id FROM leads WHERE email = :e"),
                                 {"e": email}).mappings().first()
            same_person = None
            if existing is None and pkey:
                same_person = c.execute(text("SELECT id, email, email_confidence, score, trace_id FROM leads WHERE person_key = :k "
                                             "ORDER BY id LIMIT 1"), {"k": pkey}).mappings().first()
            hit = existing or same_person
            if hit:
                lid, old_rank = int(hit["id"]), _CONF_RANK[hit["email_confidence"]]
                otid = hit["trace_id"] or tid
                better = rank > old_rank
                for src in _sources_of(d):
                    _add_source(c, lid, run_id, *src)
                if better:
                    c.execute(text("UPDATE leads SET email=:e, email_confidence=:c, name=COALESCE(NULLIF(:n,''),name), role=COALESCE(NULLIF(:r,''),role), "
                                   f"source_url=:s, context_snippet=:x, score=:sc, score_reasons_json=:sr, trace_id=:t, person_key=COALESCE(person_key,:k), updated_at={_NOW} WHERE id=:i"),
                              {"e": email, "c": d.email_confidence.value, "n": d.name, "r": d.role, "s": d.source_url, "x": d.context_snippet,
                               "sc": score, "sr": reasons_json, "t": otid, "k": pkey, "i": lid})
                    _add_trace(c, otid, run_id, [("upgrade", f"Replaced {hit['email']} ({hit['email_confidence']}) with {email} "
                                                             f"({d.email_confidence.value}): a better address for the same {'address' if existing else 'person'}")] + d.trace
                               + [("score", f"Rescored {score}/100: " + "; ".join(f"{x.label} +{x.points}" for x in reasons))])
                    res.upgraded += 1
                else:
                    if existing is None:
                        _add_trace(c, otid, run_id, [("duplicate", f"{email} is another address for the same person; kept {hit['email']}")])
                    elif (hit["score"] or 0) < score:
                        c.execute(text("UPDATE leads SET score=:sc, score_reasons_json=:sr WHERE id=:i"), {"sc": score, "sr": reasons_json, "i": lid})
                    res.duplicates += 1
                slog.log_event("lead.merged", trace_id=otid, run_id=run_id, email_domain=domains.split_email(email)[1], upgraded=better)
                continue
            r = c.execute(text(
                "INSERT INTO leads (name, email, role, company, domain, source_url, context_snippet, email_confidence, mode, "
                "notes, search_run_id, score, score_reasons_json, trace_id, person_key, updated_at) "
                f"VALUES (:n, :e, :r, :co, :d, :s, :x, :c, :m, :no, :run, :sc, :sr, :t, :k, {_NOW})"),
                {"n": d.name, "e": email, "r": d.role, "co": d.company, "d": d.domain, "s": d.source_url, "x": d.context_snippet,
                 "c": d.email_confidence.value, "m": mode, "no": d.notes or None, "run": run_id, "sc": score, "sr": reasons_json,
                 "t": tid, "k": pkey})
            lead_id = int(r.lastrowid)
            for src in _sources_of(d):
                _add_source(c, lead_id, run_id, *src)
            _add_trace(c, tid, run_id, list(d.trace) + [("score", f"Scored {score}/100: " + "; ".join(f"{x.label} +{x.points}" for x in reasons))])
        slog.log_event("lead.created", trace_id=tid, run_id=run_id, email_domain=domains.split_email(email)[1],
                       confidence=d.email_confidence.value, score=score, source=d.source_url)
        row = {"id": lead_id, "name": d.name, "email": email, "role": d.role, "company": d.company, "domain": d.domain,
               "email_confidence": d.email_confidence.value, "source_url": d.source_url, "score": score}
        res.new.append(row)
        events.emit("lead", {**row, "run_id": run_id}, engine)
    return res


class BulkRequest(BaseModel):
    action: Literal["delete", "set_status", "suppress"]
    ids: list[int] | None = None
    filter: LeadFilter | None = None  # "select all matching"
    status: LeadStatus | None = None


def _target_ids(req: BulkRequest, engine: Engine) -> list[int]:
    if req.ids is not None:
        return req.ids
    where, params, expanding = _where(req.filter or LeadFilter())
    with engine.connect() as c:
        stmt, p = _bind(f"SELECT id FROM leads{where}", params, expanding)
        return [r[0] for r in c.execute(stmt, p)]


def bulk_apply(req: BulkRequest, engine: Engine | None = None) -> dict:
    """delete: only leads with no drafts or messages. suppress: add to the suppression list (reason
    "manual", removable by the user) and close the lead. set_status: any valid status."""
    engine = engine or db.get_engine()
    ids = _target_ids(req, engine)
    affected = skipped = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        with engine.begin() as c:
            if req.action == "delete":
                stmt = text("SELECT id FROM leads WHERE id IN :ids AND id NOT IN (SELECT lead_id FROM drafts) "
                            "AND id NOT IN (SELECT lead_id FROM messages)").bindparams(bindparam("ids", expanding=True))
                deletable = [r[0] for r in c.execute(stmt, {"ids": chunk})]
                if deletable:
                    c.execute(text("DELETE FROM leads WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)), {"ids": deletable})
                affected += len(deletable)
                skipped += len(chunk) - len(deletable)
            elif req.action == "set_status":
                if req.status is None:
                    raise ValueError("status is required")
                c.execute(text("UPDATE leads SET status = :s, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id IN :ids")
                          .bindparams(bindparam("ids", expanding=True)), {"s": req.status.value, "ids": chunk})
                affected += len(chunk)
            else:
                emails = [r[0] for r in c.execute(text("SELECT email FROM leads WHERE id IN :ids")
                                                  .bindparams(bindparam("ids", expanding=True)), {"ids": chunk})]
                for e in emails:
                    c.execute(text("INSERT OR IGNORE INTO suppression (email, reason) VALUES (:e, :r)"), {"e": e, "r": db.MANUAL_REASON})
                c.execute(text("UPDATE leads SET status = 'closed', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id IN :ids")
                          .bindparams(bindparam("ids", expanding=True)), {"ids": chunk})
                affected += len(emails)
    events.emit("lead.updated", {"bulk": True}, engine)
    return {"affected": affected, "skipped": skipped}


def stats_json(d: dict) -> str:
    return json.dumps(d)
