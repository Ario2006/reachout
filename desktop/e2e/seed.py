"""Seed a throwaway REACHOUT_HOME for the Playwright tests. Run with the repo venv; PYTHONPATH=repo root."""
import json
import random
import sys

from sqlalchemy import text

from backend.core import config, db, leads, models
from backend.core.extractor import LeadDraft
from backend.core.models import EmailConfidence

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
config.ensure_dirs()
engine = db.get_engine()
db.migrate(engine)
db.save_settings(models.AppSettings(first_run_complete=True, legal_acknowledged=True, display_name="Tester"), engine)

plan = {"mode": "internship", "target_roles": ["founder"], "industries": ["AI"], "regions": ["Bangalore"], "keywords": ["seed"],
        "search_queries": ["ai startup bangalore careers", "top ai startups in bangalore"]}
stats = {"queries_total": 12, "queries_done": 12, "search_hits": 94, "domains_total": 3, "domains_done": 3, "pages": 17,
         "emails_found": 9, "people_found": 4, "leads_new": N + 3, "leads_dup": 2, "leads_suppressed": 1, "leads_dropped": 1,
         "by_confidence": {"published_personal": 2, "published_generic": N, "guessed": 1}}
with engine.begin() as c:
    c.execute(text("INSERT INTO search_runs (id, request_text, plan_json, mode, state, stage, stats_json, leads_found, pages_fetched, finished_at) "
                   "VALUES (1, 'AI/ML internships at startups in Bangalore', :p, 'internship', 'succeeded', 'done', :s, :n, 17, datetime('now'))"),
              {"p": json.dumps(plan), "s": json.dumps(stats), "n": N + 3})
    for d, st, pg, em, ld in [("acme.ai", "done", 6, 4, 3), ("slowsite.io", "failed", 0, 0, 0), ("beta.dev", "done", 4, 2, 0)]:
        c.execute(text("INSERT INTO run_domains (run_id, domain, state, pages, emails, leads, note) VALUES (1, :d, :s, :p, :e, :l, :n)"),
                  {"d": d, "s": st, "p": pg, "e": em, "l": ld, "n": "Could not fetch" if st == "failed" else None})
    for m in ["Starting: AI/ML internships at startups in Bangalore (internship)", "Searched “ai startup bangalore careers”: 10 results",
              "acme.ai/team: 4 email(s), 3 people", "acme.ai: +3 lead(s)", "Finished: 5003 new leads from 3 sites in 412s"]:
        c.execute(text("INSERT INTO run_log (run_id, message) VALUES (1, :m)"), {"m": m})

special = [
    LeadDraft(email="priya@acme.ai", name="Priya Sharma", role="Co-founder & CEO", company="Acme AI", domain="acme.ai",
              source_url="https://acme.ai/team", context_snippet="Priya Sharma, Co-founder & CEO. Email Priya to talk about internships.",
              email_confidence=EmailConfidence.published_personal),
    LeadDraft(email="ana@acme.ai", name="Ana Li", role="CTO", company="Acme AI", domain="acme.ai", source_url="https://acme.ai/team",
              context_snippet="Ana Li, CTO", email_confidence=EmailConfidence.guessed, notes="Guessed (first@) from a listed person; not verified"),
    LeadDraft(email="careers@acme.ai", company="Acme AI", domain="acme.ai", source_url="https://acme.ai/careers",
              context_snippet="Hiring? Write to careers@acme.ai.", email_confidence=EmailConfidence.published_generic),
]
leads.ingest(special, run_id=1, mode="internship", engine=engine)

# Bulk rows via plain SQL: ingest() emits an event per lead, which is pointless for 5,000 fixtures.
rng = random.Random(7)
firsts = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Isha", "Vikram", "Anaya", "Arjun", "Neha"]
lasts = ["Iyer", "Nair", "Rao", "Gupta", "Shah", "Mehta", "Reddy", "Das", "Khan", "Bose"]
rows = []
for i in range(N):
    f, l = rng.choice(firsts), rng.choice(lasts)
    conf = rng.choices(["published_personal", "published_generic", "guessed"], [5, 4, 1])[0]
    rows.append({"n": f"{f} {l}" if conf != "published_generic" else "", "e": f"{f.lower()}.{l.lower()}{i}@company{i % 400}.io",
                 "r": rng.choice(["CEO", "CTO", "Head of Eng", "Recruiter", ""]), "c": f"Company {i % 400:03d}", "d": f"company{i % 400}.io",
                 "s": f"https://company{i % 400}.io/team", "conf": conf, "st": rng.choices(["new", "sent", "replied"], [8, 3, 1])[0]})
with engine.begin() as c:
    c.execute(text("INSERT INTO leads (name, email, role, company, domain, source_url, context_snippet, email_confidence, mode, status, search_run_id, updated_at) "
                   "VALUES (:n, :e, :r, :c, :d, :s, 'seed', :conf, 'internship', :st, 1, strftime('%Y-%m-%dT%H:%M:%fZ','now'))"), rows)
print(f"seeded {N + len(special)} leads")
