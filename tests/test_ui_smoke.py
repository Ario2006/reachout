"""Render each page headlessly with Streamlit's AppTest and assert nothing raises."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.core import db

ROOT = Path(__file__).resolve().parent.parent / "app"
PAGES = ["pages/1_Find_Leads.py", "pages/2_Compose.py", "pages/3_Send.py",
         "pages/4_Dashboard.py", "pages/5_Settings.py", "pages/6_Setup_Wizard.py"]


@pytest.fixture(autouse=True)
def _isolated(home, memory_keyring, monkeypatch):
    from app.core import llm

    monkeypatch.setattr(llm, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(llm, "is_installed", lambda: False)
    import streamlit as st

    st.cache_resource.clear()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(str(ROOT / page), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]


def test_home_redirects_to_wizard_until_first_run():
    at = AppTest.from_file(str(ROOT / "main.py"), default_timeout=30).run()
    # switch_page ends the script run; the wizard must not be marked complete.
    assert not db.load_settings().first_run_complete


def test_home_renders_after_first_run():
    from app.core import config
    from app.core.models import AppSettings

    config.ensure_dirs()
    eng = db.get_engine()
    db.migrate(eng)
    db.save_settings(AppSettings(first_run_complete=True), eng)
    at = AppTest.from_file(str(ROOT / "main.py"), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]


def test_settings_save_clamps_cap_and_warns():
    at = AppTest.from_file(str(ROOT / "pages/5_Settings.py"), default_timeout=30).run()
    cap = next(n for n in at.number_input if n.label.startswith("Daily cap"))
    cap.set_value(500)
    next(b for b in at.button if b.label == "Save settings").click()
    at.run()
    assert not at.exception
    assert db.load_settings().daily_cap == 100
    assert any("hard ceiling" in w.value for w in at.warning)
