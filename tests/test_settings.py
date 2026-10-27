import pytest
from pydantic import ValidationError
from sqlalchemy import text

from backend.core import db
from backend.core.models import AppSettings, risk_warnings


def test_defaults_match_spec(engine):
    s = db.load_settings(engine)
    assert s.ollama_model == "qwen2.5:3b-instruct"
    assert (s.num_ctx, s.keep_alive) == (4096, "5m")
    assert (s.daily_cap, s.delay_min_s, s.delay_max_s) == (25, 60, 180)
    assert (s.window_start, s.window_end, s.send_days) == ("09:00", "18:00", [0, 1, 2, 3, 4, 5])
    assert s.max_pages_per_domain == 6 and s.fetch_timeout_s == 15
    assert (s.max_leads_per_site, s.max_domains_per_run, s.global_concurrency) == (5, 60, 30)
    assert (s.page_cache_ttl_days, s.crawler_contact, s.browser_tier_enabled) == (14, "", False)
    assert s.allow_guessed_emails is False


def test_roundtrip(engine):
    s = AppSettings(display_name="Aryan", daily_cap=30, gmail_address=" Me@Gmail.com ")
    db.save_settings(s, engine)
    got = db.load_settings(engine)
    assert got.display_name == "Aryan" and got.daily_cap == 30
    assert got.gmail_address == "me@gmail.com"


def test_daily_cap_clamped_to_hard_ceiling():
    assert AppSettings(daily_cap=5000).daily_cap == 100


@pytest.mark.parametrize("kw", [
    {"window_start": "9am"},
    {"window_start": "18:00", "window_end": "09:00"},
    {"delay_min_s": 100, "delay_max_s": 50},
    {"send_days": []},
    {"send_days": [7]},
    {"search_source": "searxng"},
    {"daily_cap": 0},
])
def test_invalid_settings_rejected(kw):
    with pytest.raises(ValidationError):
        AppSettings(**kw)


def test_corrupt_stored_value_falls_back_to_default(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO settings VALUES ('num_ctx', '\"not a number\"')"))
        c.execute(text("INSERT INTO settings VALUES ('display_name', '\"Ok\"')"))
    s = db.load_settings(engine)
    assert s.num_ctx == 4096 and s.display_name == "Ok"


def test_no_secret_fields_in_settings():
    for name in AppSettings.model_fields:
        assert "password" not in name and "secret" not in name and "token" not in name


def test_risk_warnings():
    assert [w for w in risk_warnings(AppSettings()) if "contact" not in w] == []
    assert any("contact" in w for w in risk_warnings(AppSettings())) and not any("contact" in w for w in risk_warnings(AppSettings(crawler_contact="me@x.org")))
    assert risk_warnings(AppSettings(daily_cap=30))
    assert risk_warnings(AppSettings(delay_min_s=10, delay_max_s=20))
    assert risk_warnings(AppSettings(allow_guessed_emails=True))
