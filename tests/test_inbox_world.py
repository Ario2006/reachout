"""Shared helpers for the tracking tests (no tests here): .eml fixtures and a small world of leads we emailed."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, text

from backend.core import db, jobs, services
from backend.core.models import AppSettings
from tests import factories

EML = Path(__file__).parent / "fixtures" / "eml"
ME = "aryan.reachout@gmail.com"


def eml(name: str) -> bytes:
    return (EML / f"{name}.eml").read_bytes()


def configure(engine: Engine, **kw) -> None:
    db.save_settings(AppSettings(gmail_address=ME, **kw), engine)


def sent_to(engine: Engine, tag: str, person: str = "x", *, campaign_id: int | None = None, kind: str = "initial",
            sent_at: str = "2026-10-27T04:30:00Z", status: str = "sent", message_id: str | None = None) -> tuple[int, int]:
    """A lead `person@tag.test` we emailed (Message-ID <sent-tag-1@reachout.test>, subject 'Internship at Tag')."""
    email = f"{person}@{tag}.test"
    with engine.connect() as c:
        row = c.execute(text("SELECT id FROM leads WHERE email = :e"), {"e": email}).first()
    lead_id = row[0] if row else factories.lead(engine, email=email, status=status, company=tag.title())
    campaign_id = campaign_id or factories.campaign(engine, state="running")
    with engine.begin() as c:
        mid = c.execute(text(
            "INSERT INTO messages (lead_id, campaign_id, kind, message_id, subject, sent_at, state, body, recipient_email) "
            "VALUES (:l, :c, :k, :m, :s, :at, 'sent', 'Hi, body.', :e)"),
            {"l": lead_id, "c": campaign_id, "k": kind, "m": message_id or f"<sent-{tag}-1@reachout.test>",
             "s": f"Internship at {tag.title()}", "at": sent_at, "e": email}).lastrowid
    return lead_id, mid


def run_check(engine: Engine) -> dict:
    """Runs one inbox_check through the real job runner (so the job ends 'succeeded') and returns its result."""
    from backend.core.inbox_poller import run_inbox_check

    job = jobs.create_job("inbox_check", {}, engine)
    jobs.run_job(jobs.claim_next(engine), {"inbox_check": run_inbox_check}, engine)
    done = jobs.get_job(job["id"], engine)
    assert done["state"] == "succeeded", done["error"]
    return done["result"]


def use_fakes() -> services.Fakes:
    f = services.fakes()
    services.use(f)
    return f


def lead_status(engine: Engine, lead_id: int) -> str:
    with engine.connect() as c:
        return c.execute(text("SELECT status FROM leads WHERE id = :i"), {"i": lead_id}).scalar_one()


def events_of(engine: Engine, lead_id: int) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text("SELECT * FROM events WHERE lead_id = :i ORDER BY id"), {"i": lead_id}).mappings()]
