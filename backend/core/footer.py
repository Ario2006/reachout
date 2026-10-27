"""The opt-out footer. Applied at SEND time by the sender (never trusted to the draft text) and by the preview, so
what the user previews is what goes out. Idempotent."""
from __future__ import annotations

from backend.core.models import AppSettings, Mode

MANDATORY_MODES = (Mode.service, Mode.investor)  # the footer cannot be switched off for these
DEFAULT_FOOTER = "If you'd rather not hear from me, just reply 'stop' and I won't email again."


def footer_text(settings: AppSettings, mode: Mode | str | None) -> str:
    """Name line plus the opt-out sentence. Empty only for an internship campaign whose footer the user cleared."""
    sentence = settings.footer_text.strip()
    mode = Mode(mode) if mode else None
    if not sentence:
        if mode is None or mode in MANDATORY_MODES:
            sentence = DEFAULT_FOOTER
        else:
            return ""
    name = settings.display_name.strip()
    return f"{name}\n{sentence}" if name else sentence


def ensure_footer(body: str, settings: AppSettings, mode: Mode | str | None) -> str:
    foot = footer_text(settings, mode)
    body = body.rstrip()
    if not foot or body.endswith(foot):
        return body + "\n" if body else body
    return f"{body}\n\n-- \n{foot}\n"
