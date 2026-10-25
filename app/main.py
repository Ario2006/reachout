"""Streamlit entry point (home page). Run via ./run.sh."""
from __future__ import annotations

import streamlit as st

from app.core import db, llm, secrets
from app.ui import LEGAL_NOTICE, init_page

engine = init_page("Home")
s = db.load_settings(engine)

if not s.first_run_complete:
    st.switch_page("pages/6_Setup_Wizard.py")

st.caption("Local, free outreach. Nothing is sent without your approval.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Ollama", "running" if llm.is_running() else "down")
c2.metric("Model", s.ollama_model)
c3.metric("Gmail", s.gmail_address or "not set")
c4.metric("App Password", "in Keychain" if secrets.has_app_password(s.gmail_address) else "missing")

st.divider()
st.markdown(LEGAL_NOTICE)
st.caption("Open/click tracking is intentionally not supported: it is unreliable and needs a public server.")
