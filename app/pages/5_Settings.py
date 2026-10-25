from __future__ import annotations

from datetime import time

import streamlit as st
from pydantic import ValidationError

from app.core import config, db
from app.core.models import AppSettings, risk_warnings
from app.ui import init_page, render_gmail_form

engine = init_page("Settings", "⚙️")

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _t(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))


st.subheader("Gmail")
st.caption(
    "Use a separate Gmail account for cold outreach. The App Password goes to the macOS "
    "Keychain and is never shown again."
)
render_gmail_form(engine, "settings")
s = db.load_settings(engine)

st.subheader("Everything else")
with st.form("settings"):
    st.markdown("**Sender**")
    display_name = st.text_input("Display name (real name)", value=s.display_name)
    footer_text = st.text_area("Email footer", value=s.footer_text, height=80,
                               help="Mandatory in service and investor modes.")

    st.markdown("**LLM (Ollama)**")
    c1, c2, c3 = st.columns(3)
    ollama_model = c1.text_input("Model", value=s.ollama_model,
                                 help=f"Fallbacks: {', '.join(config.FALLBACK_MODELS)}")
    num_ctx = c2.number_input("num_ctx", 1024, 8192, s.num_ctx, step=512)
    keep_alive = c3.text_input("keep_alive", value=s.keep_alive)

    st.markdown("**Sending**")
    c1, c2, c3 = st.columns(3)
    daily_cap = c1.number_input(f"Daily cap (hard max {config.HARD_DAILY_CEILING})", 1, 1000, s.daily_cap)
    delay_min = c2.number_input("Min delay between sends (s)", 0, 3600, s.delay_min_s)
    delay_max = c3.number_input("Max delay between sends (s)", 0, 3600, s.delay_max_s)
    c1, c2, c3 = st.columns(3)
    w_start = c1.time_input("Window start", _t(s.window_start))
    w_end = c2.time_input("Window end", _t(s.window_end))
    days = c3.multiselect("Send days", DAYS, default=[DAYS[d] for d in s.send_days])
    allow_guessed = st.checkbox(
        "Allow sending to guessed addresses (MX-checked only, never verified)",
        value=s.allow_guessed_emails,
    )

    st.markdown("**Crawl and search**")
    c1, c2, c3, c4 = st.columns(4)
    max_pages = c1.number_input("Max pages per domain", 1, 20, s.max_pages_per_domain)
    crawl_min = c2.number_input("Crawl delay min (s)", 0.5, 30.0, s.crawl_delay_min_s, step=0.5)
    crawl_max = c3.number_input("Crawl delay max (s)", 0.5, 30.0, s.crawl_delay_max_s, step=0.5)
    timeout = c4.number_input("Fetch timeout (s)", 5, 60, s.fetch_timeout_s)
    c1, c2 = st.columns(2)
    search_source = c1.selectbox("Search source", ["ddgs", "searxng"],
                                 index=["ddgs", "searxng"].index(s.search_source))
    searxng_url = c2.text_input("SearXNG URL (optional)", value=s.searxng_url,
                                placeholder="http://localhost:8080")

    submitted = st.form_submit_button("Save settings", type="primary")

if submitted:
    try:
        new = s.model_copy(update={
            "display_name": display_name.strip(),
            "footer_text": footer_text.strip(),
            "ollama_model": ollama_model.strip(),
            "num_ctx": int(num_ctx),
            "keep_alive": keep_alive.strip(),
            "daily_cap": int(daily_cap),
            "delay_min_s": int(delay_min),
            "delay_max_s": int(delay_max),
            "window_start": w_start.strftime("%H:%M"),
            "window_end": w_end.strftime("%H:%M"),
            "send_days": [DAYS.index(d) for d in days],
            "allow_guessed_emails": allow_guessed,
            "max_pages_per_domain": int(max_pages),
            "crawl_delay_min_s": float(crawl_min),
            "crawl_delay_max_s": float(crawl_max),
            "fetch_timeout_s": int(timeout),
            "search_source": search_source,
            "searxng_url": searxng_url.strip(),
        })
        validated = AppSettings.model_validate(new.model_dump())  # runs all validators
        db.save_settings(validated, engine)
        if int(daily_cap) > config.HARD_DAILY_CEILING:
            st.warning(f"Daily cap limited to the hard ceiling of {config.HARD_DAILY_CEILING}.")
        st.success("Settings saved.")
        for w in risk_warnings(validated):
            st.warning(w)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "settings"
            st.error(f"{loc}: {err['msg']}")

st.subheader("Suppression list")
st.caption("Read-only. Addresses here are never emailed, including follow-ups. "
           "Entries are added automatically on hard bounces and opt-outs.")
rows = db.list_suppression(engine)
if rows:
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.write("Empty.")

st.caption(f"Data folder: `{config.home()}`")
