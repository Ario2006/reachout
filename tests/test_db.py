from sqlalchemy.exc import IntegrityError
import sqlite3

import pytest
from sqlalchemy import text

from backend.core import db


def test_migrations_apply_and_are_idempotent(home):
    eng = db.get_engine()
    versions = [v for v, _ in db._migration_files()]
    assert db.migrate(eng) == versions and versions == list(range(1, len(versions) + 1))
    assert db.migrate(eng) == []
    assert db.schema_version(eng) == versions[-1]


def test_all_spec_tables_exist(engine):
    with engine.connect() as c:
        names = {r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    for t in ["leads", "search_runs", "templates", "campaigns", "drafts", "messages",
              "events", "suppression", "settings", "jobs", "event_log", "run_domains", "run_log", "page_cache", "domain_state", "crawl_urls", "lead_sources", "trace_events", "profile_fields", "app_state", "imap_state"]:
        assert t in names


def test_wal_and_foreign_keys(engine):
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_lead_email_unique_and_lowercase(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO leads (email) VALUES ('a@x.com')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO leads (email) VALUES ('a@x.com')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO leads (email) VALUES ('B@x.com')"))


def test_invalid_status_rejected(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO leads (email, status) VALUES ('a@x.com', 'bogus')"))


def test_campaign_cap_check_constraint(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO campaigns (name, daily_cap) VALUES ('c', 101)"))


def test_suppression_idempotent_case_insensitive(engine):
    db.add_suppression("Bad@Example.com", "bounce", engine)
    db.add_suppression("bad@example.com", "opt_out", engine)
    rows = db.list_suppression(engine)
    assert len(rows) == 1 and rows[0]["reason"] == "bounce"
    assert db.is_suppressed("BAD@example.COM", engine)
    assert not db.is_suppressed("ok@example.com", engine)


def test_failed_migration_rolls_back(home, tmp_path, monkeypatch):
    bad = tmp_path / "migs"
    bad.mkdir()
    (bad / "001_bad.sql").write_text("CREATE TABLE t (a); CREATE TABLE t (a);")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", bad)
    eng = db.get_engine()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(eng)
    assert db.schema_version(eng) == 0
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM sqlite_master WHERE name='t'")).scalar() == 0
