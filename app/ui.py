"""Shared Streamlit helpers: page bootstrap, sidebar status, Gmail credential form."""
from __future__ import annotations

import streamlit as st
from keyring.errors import KeyringError
from pydantic import ValidationError

from app.core import config, db, secrets
from app.core.models import AppSettings

LEGAL_NOTICE = """\
**Before you send anything**

- **US (CAN-SPAM):** use your real name, a truthful subject, and honour opt-outs promptly.
  Reachout adds an opt-out line to every email and suppresses anyone who asks to stop.
- **EU / UK (GDPR, PECR):** cold emailing individuals is heavily restricted. Avoid emailing
  individuals in the EU/UK, especially in `service` mode.
- **India (DPDP Act):** collect only publicly published business contact details and keep the
  source URL (Reachout stores it for every lead) so you can say why you hold an address.
- Use a **separate Gmail account** for cold outreach, not your primary one.
"""

PAGE_ICON = "📬"


@st.cache_resource
def _bootstrap():
    config.ensure_dirs()
    engine = db.get_engine()
    db.migrate(engine)
    return engine


def init_page(title: str, icon: str = PAGE_ICON):
    """Call first on every page. Returns the SQLAlchemy engine (migrated)."""
    st.set_page_config(page_title=f"Reachout - {title}", page_icon=icon, layout="wide")
    engine = _bootstrap()
    with st.sidebar:
        alive, pid, age = db.worker_status(engine=engine)
        if alive:
            st.success(f"Worker running (pid {pid})", icon="✅")
        else:
            st.warning("Worker not running. Start the app with ./run.sh", icon="⚠️")
    st.title(f"{icon} {title}")
    return engine


def placeholder(engine, milestone: int, what: str) -> None:
    st.info(f"{what} is built in **Milestone {milestone}**.")


def render_gmail_form(engine, key: str) -> AppSettings:
    """Gmail address + App Password. The password goes to the Keychain only."""
    s = db.load_settings(engine)
    with st.form(f"gmail_{key}"):
        addr = st.text_input("Gmail address", value=s.gmail_address, placeholder="you@gmail.com")
        saved = secrets.has_app_password(addr) if addr else False
        pw = st.text_input(
            "App Password",
            type="password",
            placeholder="saved in Keychain, leave blank to keep" if saved else "16-character App Password",
            help="Google Account > Security > 2-Step Verification > App passwords. "
            "Stored in the macOS Keychain, never in a file or the database.",
        )
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("Save", type="primary")
        remove = c2.form_submit_button("Remove saved password")
    if save:
        try:
            new = s.model_copy(update={"gmail_address": addr})
            AppSettings.model_validate(new.model_dump())
            if not addr.strip():
                st.error("Enter your Gmail address.")
            else:
                if s.gmail_address and s.gmail_address != addr.strip().lower() and not pw:
                    st.warning("Address changed: enter the App Password again for the new address.")
                if pw:
                    secrets.set_app_password(addr, pw)
                db.save_settings(AppSettings.model_validate(new.model_dump()), engine)
                st.success("Saved. Password stored in macOS Keychain." if pw else "Address saved.")
        except (KeyringError, ValidationError, ValueError) as e:
            st.error(f"Could not save: {e}")
    if remove:
        if secrets.delete_app_password(addr):
            st.success("Removed from Keychain.")
        else:
            st.info("No saved password to remove.")
    return db.load_settings(engine)
