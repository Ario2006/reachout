import pytest
from sqlalchemy import text

from backend.core import db, events, exports, leads, validator
from backend.core.extractor import LeadDraft
from backend.core.models import EmailConfidence, LeadStatus

P, G, X = EmailConfidence.published_personal, EmailConfidence.published_generic, EmailConfidence.guessed


def D(email, name="", conf=P, company="Acme", role="", domain="acme.ai", **kw):
    return LeadDraft(email=email, name=name, role=role, company=company, domain=domain, source_url="https://acme.ai/team",
                     context_snippet="ctx", email_confidence=conf, **kw)


def seed(engine, n=3):
    return leads.ingest([D(f"p{i}@acme.ai", f"Person {i}", company=f"Co{i}") for i in range(n)], run_id=None, mode="internship", engine=engine)


def test_ingest_inserts_dedupes_skips_suppressed_and_emits_events(engine):
    db.add_suppression("blocked@acme.ai", "opt_out", engine)
    r = leads.ingest([D("A@Acme.ai", "A"), D("a@acme.ai", "A again"), D("blocked@acme.ai"), D("b@acme.ai")], run_id=None, mode="service", engine=engine)
    assert [x["email"] for x in r.new] == ["a@acme.ai", "b@acme.ai"] and r.duplicates == 1 and r.suppressed == 1
    rows, total = leads.query_leads(leads.LeadFilter(), engine=engine)
    assert total == 2 and {x["mode"] for x in rows} == {"service"}
    assert len([e for e in events.read_since(0, 100, engine) if e["type"] == "lead"]) == 2


def test_published_address_upgrades_a_guess_but_never_downgrades(engine):
    leads.ingest([D("jane@acme.ai", "Jane", conf=X)], run_id=None, mode=None, engine=engine)
    r = leads.ingest([D("jane@acme.ai", "Jane Doe", conf=P, role="CTO")], run_id=None, mode=None, engine=engine)
    row = leads.query_leads(leads.LeadFilter(), engine=engine)[0][0]
    assert r.upgraded == 1 and row["email_confidence"] == "published_personal" and row["role"] == "CTO"
    r = leads.ingest([D("jane@acme.ai", "Jane", conf=X)], run_id=None, mode=None, engine=engine)
    assert r.duplicates == 1 and leads.query_leads(leads.LeadFilter(), engine=engine)[0][0]["email_confidence"] == "published_personal"


def test_filters_search_and_pagination(engine):
    leads.ingest([D("a@acme.ai", "Ann", G, company="Alpha"), D("b@beta.io", "Bob", P, company="Beta", domain="beta.io"),
                  D("c@gamma.io", "Cy", X, company="Gamma 100%", domain="gamma.io")], run_id=None, mode="investor", engine=engine)
    q = lambda **kw: leads.query_leads(leads.LeadFilter(**kw), engine=engine)  # noqa: E731
    assert q(confidence=[X])[1] == 1 and q(confidence=[P, G])[1] == 2
    assert q(q="alpha")[1] == 1 and q(q="beta.io")[1] == 1 and q(q="Bob")[1] == 1
    assert q(q="100%")[1] == 1 and q(q="%")[1] == 1  # LIKE wildcards are escaped, not interpreted
    assert q(q="_")[1] == 0
    assert q(status=[LeadStatus.new])[1] == 3 and q(status=[LeadStatus.sent])[1] == 0
    assert q(mode=["investor"])[1] == 3
    rows, total = leads.query_leads(leads.LeadFilter(), "company:asc", offset=1, limit=1, engine=engine)
    assert total == 3 and rows[0]["company"] == "Beta"


def test_multi_sort_and_injection_safety(engine):
    leads.ingest([D("a@x.ai", "Zed", company="B"), D("b@x.ai", "Amy", company="B"), D("c@x.ai", "Bo", company="A")], run_id=None, mode=None, engine=engine)
    names = lambda s: [r["name"] for r in leads.query_leads(leads.LeadFilter(), s, engine=engine)[0]]  # noqa: E731
    assert names("company:asc,name:asc") == ["Bo", "Amy", "Zed"]
    assert names("company:asc,name:desc") == ["Bo", "Zed", "Amy"]
    assert leads.parse_sort("name; DROP TABLE leads:asc,bogus:desc") == "id DESC"
    assert leads.parse_sort("email_confidence:desc") == "email_confidence DESC"
    names("name;DROP TABLE leads")  # must not raise or execute
    assert leads.query_leads(leads.LeadFilter(), engine=engine)[1] == 3


def test_update_lead_validates_fields(engine):
    leads.ingest([D("a@acme.ai", "A")], run_id=None, mode=None, engine=engine)
    lid = leads.query_leads(leads.LeadFilter(), engine=engine)[0][0]["id"]
    out = leads.update_lead(lid, {"name": "  New Name ", "status": "closed", "email": "hacker@evil.com", "notes": "n"}, engine)
    assert out["name"] == "New Name" and out["status"] == "closed" and out["notes"] == "n" and out["email"] == "a@acme.ai"
    with pytest.raises(ValueError):
        leads.update_lead(lid, {"status": "bogus"}, engine)
    assert leads.update_lead(9999, {"name": "x"}, engine) is None


def test_lead_detail_has_run_and_timeline(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO search_runs (request_text) VALUES ('find stuff')"))
    leads.ingest([D("a@acme.ai", "A")], run_id=1, mode=None, engine=engine)
    lid = leads.query_leads(leads.LeadFilter(), engine=engine)[0][0]["id"]
    with engine.begin() as c:
        c.execute(text("INSERT INTO events (lead_id, type, snippet) VALUES (:l, 'reply', 'thanks!')"), {"l": lid})
    d = leads.get_lead(lid, engine)
    assert d["run"]["request_text"] == "find stuff" and d["context_snippet"] == "ctx"
    assert [t["type"] for t in d["timeline"]] == ["reply"]
    assert leads.query_leads(leads.LeadFilter(run_id=1), engine=engine)[1] == 1
    assert leads.query_leads(leads.LeadFilter(run_id=2), engine=engine)[1] == 0


def test_bulk_set_status_delete_suppress(engine):
    seed(engine, 4)
    ids = [r["id"] for r in leads.query_leads(leads.LeadFilter(), engine=engine)[0]]
    assert leads.bulk_apply(leads.BulkRequest(action="set_status", ids=ids[:2], status=LeadStatus.approved), engine) == {"affected": 2, "skipped": 0}
    assert leads.query_leads(leads.LeadFilter(status=[LeadStatus.approved]), engine=engine)[1] == 2
    with pytest.raises(ValueError):
        leads.bulk_apply(leads.BulkRequest(action="set_status", ids=ids), engine)

    r = leads.bulk_apply(leads.BulkRequest(action="suppress", ids=[ids[0]]), engine)
    assert r["affected"] == 1 and db.is_suppressed("p3@acme.ai", engine) or db.is_suppressed("p0@acme.ai", engine)
    assert [x for x in db.list_suppression(engine)][0]["reason"] == "manual"  # removable by the user
    assert leads.query_leads(leads.LeadFilter(status=[LeadStatus.closed]), engine=engine)[1] == 1

    assert leads.bulk_apply(leads.BulkRequest(action="delete", ids=[ids[1], ids[2]]), engine)["affected"] == 2
    assert leads.query_leads(leads.LeadFilter(), engine=engine)[1] == 2


def test_bulk_delete_protects_leads_with_drafts_or_messages(engine):
    seed(engine, 3)
    ids = [r["id"] for r in leads.query_leads(leads.LeadFilter(), engine=engine)[0]]
    with engine.begin() as c:
        c.execute(text("INSERT INTO campaigns (name) VALUES ('c')"))
        c.execute(text("INSERT INTO drafts (campaign_id, lead_id, subject, body) VALUES (1, :l, 's', 'b')"), {"l": ids[0]})
        c.execute(text("INSERT INTO messages (lead_id, campaign_id, kind) VALUES (:l, 1, 'initial')"), {"l": ids[1]})
    r = leads.bulk_apply(leads.BulkRequest(action="delete", ids=ids), engine)
    assert r == {"affected": 1, "skipped": 2} and leads.query_leads(leads.LeadFilter(), engine=engine)[1] == 2


def test_bulk_by_filter_select_all_matching(engine):
    leads.ingest([D(f"g{i}@a.ai", conf=G) for i in range(3)] + [D("p@a.ai", "P", conf=P)], run_id=None, mode=None, engine=engine)
    r = leads.bulk_apply(leads.BulkRequest(action="set_status", filter=leads.LeadFilter(confidence=[G]), status=LeadStatus.closed), engine)
    assert r["affected"] == 3 and leads.query_leads(leads.LeadFilter(status=[LeadStatus.closed]), engine=engine)[1] == 3


# ---- CSV -----------------------------------------------------------------------------------

def mxstub(bad=("nomx.ai",)):
    return validator.MxChecker(lookup=lambda d: d not in bad)


def test_export_neutralises_formula_injection_and_roundtrips(engine):
    leads.ingest([D("a@acme.ai", "=HYPERLINK(\"http://evil\",\"x\")", company="+cmd|' /C calc'!A0"),
                  D("b@acme.ai", "Normal, \"quoted\" name", company="@SUM(1)", role="-2+3")], run_id=None, mode="service", engine=engine)
    out = exports.export_csv(leads.LeadFilter(), "email:asc", engine)
    lines = out.splitlines()
    assert lines[0] == ",".join(exports.COLUMNS)
    import csv, io
    rows = list(csv.DictReader(io.StringIO(out)))
    assert rows[0]["name"].startswith("'=") and rows[0]["company"].startswith("'+")
    assert rows[1]["company"].startswith("'@") and rows[1]["role"].startswith("'-")
    assert rows[1]["name"] == 'Normal, "quoted" name'
    assert all(not any(c.startswith(("=", "+", "-", "@")) for c in r.values()) for r in rows)


def test_export_respects_filters(engine):
    leads.ingest([D("a@acme.ai", conf=G), D("b@acme.ai", "B", conf=P)], run_id=None, mode=None, engine=engine)
    out = exports.export_csv(leads.LeadFilter(confidence=[P]), "", engine)
    assert "b@acme.ai" in out and "a@acme.ai" not in out


CSV = """name,email,role,company,domain,source_url,email_confidence,mode,notes
Ann,ann@good.ai,CTO,Good,good.ai,https://good.ai/t,published_personal,internship,hello
Bad,not-an-email,,,,,,,
Dup,ann@good.ai,,,,,,,
Nomx,x@nomx.ai,,,,,,,
Sup,sup@good.ai,,,,,,,
Img,logo@2x.png,,,,,,,
Gen,info@good.ai,,Good,,,bogus_value,bogus_mode,
"""


def test_import_report_and_insert(engine):
    db.add_suppression("sup@good.ai", "bounce", engine)
    rep = exports.import_csv(CSV, engine=engine, mx=mxstub())
    assert (rep.total, rep.valid, rep.inserted) == (7, 2, 2)
    assert rep.invalid == 2 and rep.no_mx == 1 and rep.suppressed == 1 and rep.duplicates == 1
    assert {i.reason for i in rep.issues} >= {"Duplicate within the file", "On the suppression list", "Domain has no mail server (MX record)"}
    rows = {r["email"]: r for r in leads.query_leads(leads.LeadFilter(), engine=engine)[0]}
    assert rows["ann@good.ai"]["mode"] == "internship" and rows["ann@good.ai"]["email_confidence"] == "published_personal"
    assert rows["info@good.ai"]["email_confidence"] == "published_generic" and rows["info@good.ai"]["mode"] is None


def test_import_dry_run_changes_nothing(engine):
    db.add_suppression("sup@good.ai", "bounce", engine)
    rep = exports.import_csv(CSV, dry_run=True, engine=engine, mx=mxstub())
    assert rep.valid == 2 and rep.inserted == 0 and leads.query_leads(leads.LeadFilter(), engine=engine)[1] == 0


def test_import_existing_leads_count_as_duplicates(engine):
    exports.import_csv(CSV, engine=engine, mx=mxstub())
    rep = exports.import_csv(CSV, engine=engine, mx=mxstub())
    assert rep.inserted == 0 and rep.duplicates >= 3


def test_import_handles_bom_case_insensitive_headers_and_minimal_columns(engine):
    rep = exports.import_csv("﻿Email,Name\nzed@good.ai,Zed\n", engine=engine, mx=mxstub())
    assert rep.inserted == 1
    r = leads.query_leads(leads.LeadFilter(), engine=engine)[0][0]
    assert (r["name"], r["domain"]) == ("Zed", "good.ai")


def test_import_rejects_bad_files(engine):
    with pytest.raises(ValueError, match="email"):
        exports.import_csv("name,company\nA,B\n", engine=engine, mx=mxstub())
    with pytest.raises(ValueError):
        exports.import_csv("", engine=engine, mx=mxstub())
    big = "email\n" + "\n".join(f"u{i}@good.ai" for i in range(exports.MAX_IMPORT_ROWS + 1))
    with pytest.raises(ValueError, match="Too many"):
        exports.import_csv(big, engine=engine, mx=mxstub())


def test_export_then_import_roundtrip(engine):
    leads.ingest([D("a@good.ai", "Ann", P, role="CTO", company="Good", domain="good.ai")], run_id=None, mode="investor", engine=engine)
    out = exports.export_csv(leads.LeadFilter(), "", engine)
    with engine.begin() as c:
        c.execute(text("DELETE FROM leads"))
    rep = exports.import_csv(out, engine=engine, mx=mxstub())
    r = leads.query_leads(leads.LeadFilter(), engine=engine)[0][0]
    assert rep.inserted == 1 and (r["name"], r["role"], r["mode"], r["email_confidence"]) == ("Ann", "CTO", "investor", "published_personal")


def test_import_keeps_only_http_source_urls(engine):
    csv = ("email,source_url\na@good.ai,https://good.ai/team\nb@good.ai,n/a\nc@good.ai,javascript:alert(1)\n"
           "d@good.ai,file:///etc/passwd\ne@good.ai,HTTP://Good.ai/x\n")
    exports.import_csv(csv, engine=engine, mx=mxstub())
    got = {r["email"]: r["source_url"] for r in leads.query_leads(leads.LeadFilter(), engine=engine)[0]}
    assert got == {"a@good.ai": "https://good.ai/team", "b@good.ai": "", "c@good.ai": "", "d@good.ai": "", "e@good.ai": "HTTP://Good.ai/x"}
