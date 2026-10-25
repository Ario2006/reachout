"""First-run wizard checks. Each returns a Check; none raise."""
from __future__ import annotations

import imaplib
import smtplib
from dataclasses import dataclass

import psutil

from app.core import config, llm


@dataclass
class Check:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def check_data_dir() -> Check:
    try:
        config.ensure_dirs()
        probe = config.home() / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return Check("Data folder", "ok", str(config.home()))
    except OSError as e:
        return Check("Data folder", "fail", str(e), f"Make {config.home()} writable.")


def check_ollama() -> Check:
    if llm.is_running():
        return Check("Ollama running", "ok", llm.host())
    if not llm.is_installed():
        return Check("Ollama running", "fail", "Ollama is not installed.", "Run: brew install ollama")
    return Check(
        "Ollama running", "fail", "Installed but not responding.",
        "Run: brew services start ollama   (or: ollama serve)",
    )


def check_model(model: str) -> Check:
    name = "Model available"
    if not llm.is_running():
        return Check(name, "fail", "Ollama is not running.", "Start Ollama first.")
    try:
        if llm.has_model(model):
            return Check(name, "ok", model)
    except Exception as e:  # noqa: BLE001 - surface any client error to the UI
        return Check(name, "fail", f"Could not list models: {e}")
    return Check(name, "fail", f"{model} is not pulled yet.", "Use the Pull model button.")


def check_memory() -> Check:
    vm = psutil.virtual_memory()
    total, avail = vm.total / 2**30, vm.available / 2**30
    detail = f"{avail:.1f} GB available of {total:.1f} GB"
    if avail < 3.0:
        return Check(
            "Memory", "warn", detail,
            "A 3B model needs ~3 GB. Close heavy apps (browsers, IDEs) before long runs.",
        )
    return Check("Memory", "ok", detail)


def check_smtp(email: str, password: str | None, host: str = config.SMTP_HOST,
               port: int = config.SMTP_PORT) -> Check:
    name = "Gmail SMTP login"
    if not email or not password:
        return Check(name, "fail", "No address or App Password saved.", "Enter them in Settings.")
    try:
        with smtplib.SMTP_SSL(host, port, timeout=15) as s:
            s.login(email, password)
        return Check(name, "ok", f"Logged in as {email}")
    except smtplib.SMTPAuthenticationError:
        return Check(
            name, "fail", "Gmail rejected the login.",
            "Use a 16-character App Password (needs 2-Step Verification), not your normal password.",
        )
    except (OSError, smtplib.SMTPException) as e:
        return Check(name, "fail", f"Connection problem: {e}", "Check your internet connection.")


def check_imap(email: str, password: str | None, host: str = config.IMAP_HOST,
               port: int = config.IMAP_PORT) -> Check:
    name = "Gmail IMAP login"
    if not email or not password:
        return Check(name, "fail", "No address or App Password saved.", "Enter them in Settings.")
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=15)
        try:
            conn.login(email, password)
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
        return Check(name, "ok", f"Logged in as {email}")
    except imaplib.IMAP4.error:
        return Check(
            name, "fail", "Gmail rejected the login.",
            "Enable IMAP in Gmail settings (Forwarding and POP/IMAP) and use an App Password.",
        )
    except OSError as e:
        return Check(name, "fail", f"Connection problem: {e}", "Check your internet connection.")
