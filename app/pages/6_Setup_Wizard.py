from __future__ import annotations

import streamlit as st

from app.core import db, health, llm, secrets
from app.ui import LEGAL_NOTICE, init_page, render_gmail_form

engine = init_page("Setup Wizard", "🧭")
s = db.load_settings(engine)

ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌"}


def show(c: health.Check) -> None:
    st.markdown(f"{ICON[c.status]} **{c.name}**: {c.detail}")
    if c.hint and not c.ok:
        st.caption(c.hint)


st.info(
    "Tip: create a **separate Gmail account** for cold outreach so your primary account's "
    "reputation is never at risk."
)

st.subheader("1. This Mac")
local = [health.check_data_dir(), health.check_ollama(), health.check_model(s.ollama_model),
         health.check_memory()]
for c in local:
    show(c)

model_ok = local[2].ok
if local[1].ok and not model_ok:
    if st.button(f"Pull {s.ollama_model}", type="primary"):
        bar, label = st.progress(0.0), st.empty()
        try:
            for status, frac in llm.pull_model(s.ollama_model):
                label.caption(status)
                if frac is not None:
                    bar.progress(min(frac, 1.0))
            bar.progress(1.0)
            st.success("Model ready.")
            st.rerun()
        except Exception as e:  # noqa: BLE001 - show any pull error
            st.error(f"Pull failed: {e}")
st.button("Re-run checks")

st.subheader("2. Gmail")
s = render_gmail_form(engine, "wizard")
if st.button("Test SMTP and IMAP login"):
    pw = secrets.get_app_password(s.gmail_address)
    with st.spinner("Contacting Gmail..."):
        for c in (health.check_smtp(s.gmail_address, pw), health.check_imap(s.gmail_address, pw)):
            show(c)
st.caption("Gmail is optional to finish setup, but sending and tracking need it.")

st.subheader("3. Finish")
st.markdown(LEGAL_NOTICE)
can_finish = local[0].ok and local[1].ok and model_ok
if not can_finish:
    st.warning("Fix the Data folder, Ollama and Model checks above to finish.")
if st.button("Finish setup", type="primary", disabled=not can_finish):
    db.save_settings(s.model_copy(update={"first_run_complete": True}), engine)
    st.success("Setup complete. Open Home from the sidebar.")
