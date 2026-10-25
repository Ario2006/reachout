import imaplib
import smtplib

from backend.core import health, llm


class FakeSMTP:
    mode = "ok"

    def __init__(self, *a, **k):
        if FakeSMTP.mode == "conn":
            raise OSError("no route")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        if FakeSMTP.mode == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad")


class FakeIMAP:
    mode = "ok"

    def __init__(self, *a, **k):
        if FakeIMAP.mode == "conn":
            raise OSError("no route")

    def login(self, u, p):
        if FakeIMAP.mode == "auth":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def logout(self):
        pass


def test_smtp_results(monkeypatch):
    monkeypatch.setattr(health.smtplib, "SMTP_SSL", FakeSMTP)
    FakeSMTP.mode = "ok"
    assert health.check_smtp("a@gmail.com", "pw").ok
    FakeSMTP.mode = "auth"
    c = health.check_smtp("a@gmail.com", "pw")
    assert c.status == "fail" and "App Password" in c.hint
    FakeSMTP.mode = "conn"
    assert health.check_smtp("a@gmail.com", "pw").status == "fail"
    assert health.check_smtp("", None).status == "fail"


def test_imap_results(monkeypatch):
    monkeypatch.setattr(health.imaplib, "IMAP4_SSL", FakeIMAP)
    FakeIMAP.mode = "ok"
    assert health.check_imap("a@gmail.com", "pw").ok
    FakeIMAP.mode = "auth"
    assert health.check_imap("a@gmail.com", "pw").status == "fail"
    FakeIMAP.mode = "conn"
    assert health.check_imap("a@gmail.com", "pw").status == "fail"


def test_ollama_down_and_not_installed(monkeypatch):
    monkeypatch.setattr(llm, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(llm, "is_installed", lambda: False)
    c = health.check_ollama()
    assert c.status == "fail" and "brew install" in c.hint
    assert health.check_model("m").status == "fail"


def test_model_present_and_missing(monkeypatch):
    monkeypatch.setattr(llm, "is_running", lambda *a, **k: True)
    monkeypatch.setattr(llm, "list_models", lambda: ["qwen2.5:3b-instruct", "llama3.2:latest"])
    assert health.check_model("qwen2.5:3b-instruct").ok
    assert health.check_model("llama3.2").ok  # bare name matches :latest
    assert health.check_model("qwen3:4b").status == "fail"


def test_data_dir(home):
    assert health.check_data_dir().ok
    assert home.is_dir()


def test_memory_check_runs():
    assert health.check_memory().status in ("ok", "warn")


def test_ollama_host_normalised(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9999")
    assert llm.host() == "http://127.0.0.1:9999"
