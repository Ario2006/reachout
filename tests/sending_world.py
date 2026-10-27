"""Shared builders for the sender, safety and campaign tests: a configured account, approved drafts, a sender with
fakes and a seeded random generator. Everything is in SQLite and in memory; no SMTP."""
from __future__ import annotations

import random

from sqlalchemy import text

from backend.core import campaigns, db, outbox, services
from backend.core.models import AppSettings
from backend.core.sender import Sender
from tests import factories

ME = "me@gmail.com"


def configure(engine, **kw) -> AppSettings:
    base = dict(gmail_address=ME, display_name="Aryan Kumar", daily_cap=25, window_start="00:00", window_end="23:59",
                send_days=[0, 1, 2, 3, 4, 5, 6], use_recipient_timezone=False, delay_min_s=60, delay_max_s=180,
                max_per_domain_per_day=2, min_minutes_between_same_domain=60)
    base.update(kw)
    s = AppSettings(**base)
    db.save_settings(s, engine)
    return s


def fresh(engine, **settings_kw):
    """(fakes, settings) with fakes installed as the service registry and the clock set to a fixed Friday."""
    svc = services.fakes()
    services.use(svc)
    return svc, configure(engine, **settings_kw)


def make_campaign(engine, n: int = 3, state: str = "running", domains: list[str] | None = None, approved: bool = True, **kw) -> tuple[int, list[int]]:
    """A campaign with `n` leads, each with an approved draft and a queued message (as Start would leave it)."""
    cid = factories.campaign(engine, state=state, **kw)
    lead_ids = []
    for i in range(n):
        dom = domains[i] if domains else f"co{cid}x{i}.test"
        lid = factories.lead(engine, email=f"p{i}@{dom}", status="approved")
        factories.draft(engine, cid, lid, status="approved" if approved else "pending", subject=f"Hello {i}", body=f"Hi {i},\n\nI like your work.")
        lead_ids.append(lid)
    if approved:
        campaigns.enqueue_approved(cid, engine)
    return cid, lead_ids


def msg(engine, cid: int, lid: int, kind: str = "initial") -> dict:
    with engine.connect() as c:
        return dict(c.execute(text("SELECT * FROM messages WHERE campaign_id=:c AND lead_id=:l AND kind=:k"), {"c": cid, "l": lid, "k": kind}).mappings().one())


def states(engine, cid: int) -> dict[int, str]:
    with engine.connect() as c:
        return {r[0]: r[1] for r in c.execute(text("SELECT lead_id, state FROM messages WHERE campaign_id=:c"), {"c": cid})}


def camp(engine, cid: int) -> dict:
    return campaigns.get(cid, engine)  # type: ignore[return-value]


def sender(engine, seed: int = 1, **kw) -> Sender:
    kw.setdefault("idle_backoff_s", 0.0)
    return Sender(engine, rng=random.Random(seed), **kw)


def run_ticks(s: Sender, svc, n: int, step: float = 200.0) -> int:
    """Tick n times, moving the fake clock past the longest delay between ticks. Returns emails handed to the mailer."""
    sent = 0
    for _ in range(n):
        sent += s.tick()
        svc.fake_clock.advance(step)
    return sent


def suppress(engine, email: str) -> None:
    db.add_suppression(email, "test", engine)


def force_pause_all(engine, value: bool) -> None:
    outbox.set_paused_all(value, engine)
