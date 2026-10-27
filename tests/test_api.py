import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from backend.api import export_openapi  # noqa: F401  (imports cleanly)
from backend.api.main import create_app
from backend.api.routers import events as events_router
from backend.core import db, events, health, jobs, secrets

TOKEN = "t" * 32
ORIGIN = "http://localhost:5173"
PASSWORD = "zzqqxxwwvvuuttss"


@pytest.fixture
def app(engine, memory_keyring):
    return create_app(token=TOKEN, allowed_origins=[ORIGIN], engine=engine)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1:8000",
                    headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


@pytest.fixture
def anon(app):
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c


def ok_check(name="x"):
    return health.Check(name, "ok", "fine")


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(health, "check_ollama", lambda: ok_check("Ollama running"))
    monkeypatch.setattr(health, "check_model", lambda m: ok_check("Model available"))
    monkeypatch.setattr(health, "check_memory", lambda: ok_check("Memory"))


# ------------------------------------------------------------------ security

def _all_routes(app):
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            yield method.upper(), re.sub(r"\{[a-z_]+\}", "1", path)


def test_every_route_rejects_missing_and_wrong_token(app, anon):
    routes = list(_all_routes(app))
    assert len(routes) >= 15
    for method, path in routes:
        assert anon.request(method, path).status_code == 401, (method, path)
        r = anon.request(method, path, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401, (method, path)


def test_wrong_scheme_and_prefix_token_rejected(anon):
    assert anon.get("/health", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert anon.get("/health", headers={"Authorization": f"Bearer {TOKEN[:-1]}"}).status_code == 401
    assert anon.get("/health", headers={"Authorization": f"Bearer {TOKEN}x"}).status_code == 401
    assert anon.get(f"/health?token={TOKEN}").status_code == 401  # no query-string auth


def test_valid_token_accepted(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_foreign_host_rejected(app):
    with TestClient(app, base_url="http://evil.example.com",
                    headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        assert c.get("/health").status_code == 403  # DNS-rebinding guard


def test_foreign_origin_rejected_allowed_origin_passes(client):
    assert client.get("/health", headers={"Origin": "http://evil.example.com"}).status_code == 403
    assert client.get("/health", headers={"Origin": "null"}).status_code == 403
    r = client.get("/health", headers={"Origin": ORIGIN})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == ORIGIN


def test_cors_preflight(anon):
    ok = anon.options("/settings", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == ORIGIN
    bad = anon.options("/settings", headers={
        "Origin": "http://evil.example.com", "Access-Control-Request-Method": "PUT"})
    assert "access-control-allow-origin" not in bad.headers


def test_short_token_refused(engine):
    with pytest.raises(ValueError):
        with TestClient(create_app(token="short", engine=engine)):
            pass


def test_no_runtime_docs(client):
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(p).status_code == 404


def test_no_response_ever_contains_the_app_password(client, healthy):
    client.put("/settings/gmail", json={"address": "me@gmail.com", "app_password": PASSWORD})
    bodies = [
        client.get("/settings").text, client.get("/settings/gmail").text,
        client.get("/setup/status").text, client.get("/health").text,
        client.put("/settings", json={"display_name": "A"}).text,
        client.put("/settings/gmail", json={"address": "me@gmail.com", "app_password": PASSWORD}).text,
        client.post("/setup/test-gmail").text,
    ]
    assert secrets.get_app_password("me@gmail.com") == PASSWORD
    for b in bodies:
        assert PASSWORD not in b


# ------------------------------------------------------------------ settings

def test_get_settings_defaults(client):
    j = client.get("/settings").json()
    assert j["settings"]["ollama_model"] == "qwen2.5:3b-instruct"
    assert j["limits"]["hard_daily_ceiling"] == 100
    assert len(j["warnings"]) == 1 and "no contact" in j["warnings"][0]  # the only default warning: crawler contact not set
    assert j["gmail_password_set"] is False


def test_partial_update_keeps_other_fields(client):
    client.put("/settings", json={"display_name": "Aryan", "daily_cap": 20})
    j = client.put("/settings", json={"keep_alive": "10m"}).json()["settings"]
    assert (j["display_name"], j["daily_cap"], j["keep_alive"]) == ("Aryan", 20, "10m")


def test_daily_cap_clamped_with_notice_and_warning(client):
    j = client.put("/settings", json={"daily_cap": 500}).json()
    assert j["settings"]["daily_cap"] == 100
    assert any("hard ceiling" in n for n in j["notices"])
    assert any("above the safe default" in w for w in j["warnings"])
    assert client.get("/settings").json()["settings"]["daily_cap"] == 100


@pytest.mark.parametrize("body", [
    {"window_start": "9am"},
    {"window_start": "18:00", "window_end": "09:00"},
    {"delay_min_s": 100, "delay_max_s": 50},
    {"send_days": []},
    {"daily_cap": 0},
    {"search_source": "searxng"},
    {"num_ctx": "abc"},
])
def test_invalid_settings_422_and_not_saved(client, body):
    r = client.put("/settings", json=body)
    assert r.status_code == 422
    assert r.json()["detail"]
    assert client.get("/settings").json()["settings"]["window_start"] == "09:00"


@pytest.mark.parametrize("field", ["gmail_address", "first_run_complete", "legal_acknowledged", "bogus"])
def test_protected_and_unknown_fields_rejected(client, field):
    assert client.put("/settings", json={field: "x"}).status_code == 422


def test_gmail_flow(client):
    r = client.put("/settings/gmail", json={"address": "Me@Gmail.com", "app_password": "abcd efgh ijkl mnop"})
    assert r.json() == {"address": "me@gmail.com", "password_set": True}
    assert secrets.get_app_password("me@gmail.com") == "abcdefghijklmnop"
    # Address-only update keeps the stored password.
    assert client.put("/settings/gmail", json={"address": "me@gmail.com"}).json()["password_set"] is True
    assert client.get("/settings").json()["gmail_password_set"] is True
    assert client.delete("/settings/gmail/password").json()["password_set"] is False
    assert client.put("/settings/gmail", json={"address": "not-an-email"}).status_code == 422


# --------------------------------------------------------------- suppression

def test_suppression_add_list_search_idempotent(client):
    r = client.post("/suppression", json={"email": "Bad@Example.com"})
    assert r.status_code == 201 and r.json()["email"] == "bad@example.com" and r.json()["removable"]
    client.post("/suppression", json={"email": "bad@example.com"})
    client.post("/suppression", json={"email": "other@x.org"})
    assert len(client.get("/suppression").json()) == 2
    assert [e["email"] for e in client.get("/suppression", params={"q": "example"}).json()] == ["bad@example.com"]
    assert client.post("/suppression", json={"email": "nope"}).status_code == 422


def test_suppression_remove_needs_typed_confirmation(client, engine):
    client.post("/suppression", json={"email": "a@x.com"})
    assert client.delete("/suppression", params={"email": "a@x.com", "confirm": "wrong"}).status_code == 400
    assert db.is_suppressed("a@x.com", engine)
    assert client.delete("/suppression", params={"email": "a@x.com"}).status_code == 422  # confirm required
    assert client.delete("/suppression", params={"email": "a@x.com", "confirm": "A@X.com"}).status_code == 204
    assert not db.is_suppressed("a@x.com", engine)
    assert client.delete("/suppression", params={"email": "a@x.com", "confirm": "a@x.com"}).status_code == 404


@pytest.mark.parametrize("reason", ["bounce", "opt_out", "hard bounce 5.1.1"])
def test_automatic_suppressions_cannot_be_removed(client, engine, reason):
    db.add_suppression("gone@x.com", reason, engine)
    r = client.delete("/suppression", params={"email": "gone@x.com", "confirm": "gone@x.com"})
    assert r.status_code == 403
    assert db.is_suppressed("gone@x.com", engine)
    assert client.get("/suppression").json()[0]["removable"] is False


def test_manual_add_cannot_downgrade_automatic_entry(client, engine):
    db.add_suppression("gone@x.com", "opt_out", engine)
    client.post("/suppression", json={"email": "gone@x.com"})
    assert client.get("/suppression").json()[0]["reason"] == "opt_out"


# --------------------------------------------------------------------- setup

def test_setup_status_shape(client, healthy):
    j = client.get("/setup/status").json()
    assert [c["id"] for c in j["checks"]] == ["data_dir", "ollama", "model", "memory"]
    assert j["first_run_complete"] is False and j["pull_job"] is None
    assert j["gmail"] == {"address": "", "password_set": False}
    assert j["worker"]["alive"] is False


def test_skip_wizard_env(client, healthy, monkeypatch):
    monkeypatch.setenv("REACHOUT_SKIP_WIZARD", "1")
    assert client.get("/setup/status").json()["first_run_complete"] is True


def test_models_endpoint(client, monkeypatch):
    from backend.core import llm

    monkeypatch.setattr(llm, "is_running", lambda *a, **k: False)
    assert client.get("/setup/models").json() == {"ollama_running": False, "installed": []}
    monkeypatch.setattr(llm, "is_running", lambda *a, **k: True)
    monkeypatch.setattr(llm, "list_models", lambda: ["b:1", "a:1"])
    assert client.get("/setup/models").json() == {"ollama_running": True, "installed": ["a:1", "b:1"]}
    monkeypatch.setattr(llm, "list_models", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert client.get("/setup/models").json()["ollama_running"] is False


def test_pull_model_creates_job_and_is_idempotent(client, engine):
    r = client.post("/setup/pull-model")
    assert r.status_code == 202
    j = r.json()
    assert j["kind"] == "pull_model" and j["state"] == "queued"
    assert j["payload"] == {"model": "qwen2.5:3b-instruct"}
    assert client.post("/setup/pull-model", json={"model": "qwen2.5:3b-instruct"}).json()["id"] == j["id"]
    other = client.post("/setup/pull-model", json={"model": "llama3.2:3b"}).json()
    assert other["id"] != j["id"]
    assert [x["id"] for x in client.get("/jobs", params={"active": True}).json()] == [other["id"], j["id"]]


@pytest.mark.parametrize("name", ["", "bad name", "x;rm -rf /", "-lead", "a" * 0 + "../etc"])
def test_pull_model_rejects_bad_names(client, name):
    r = client.post("/setup/pull-model", json={"model": name})
    # An empty string falls back to the configured model; everything else is invalid.
    assert r.status_code == (202 if name == "" else 422)


def test_status_reports_active_pull_job(client, healthy):
    job = client.post("/setup/pull-model").json()
    assert client.get("/setup/status").json()["pull_job"]["id"] == job["id"]


def test_test_gmail_uses_keychain_password(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(health, "check_smtp", lambda a, p: seen.update(smtp=(a, p)) or ok_check("SMTP"))
    monkeypatch.setattr(health, "check_imap", lambda a, p: seen.update(imap=(a, p)) or ok_check("IMAP"))
    client.put("/settings/gmail", json={"address": "me@gmail.com", "app_password": PASSWORD})
    j = client.post("/setup/test-gmail").json()
    assert j["smtp"]["status"] == "ok" and j["imap"]["id"] == "imap"
    assert seen["smtp"] == ("me@gmail.com", PASSWORD) and seen["imap"] == ("me@gmail.com", PASSWORD)


def test_complete_requires_legal_ack_and_passing_checks(client, healthy, monkeypatch):
    body = {"display_name": "Aryan", "footer_text": "stop?", "legal_acknowledged": False}
    assert client.post("/setup/complete", json=body).status_code == 422
    monkeypatch.setattr(health, "check_model", lambda m: health.Check("Model available", "fail", "no"))
    body["legal_acknowledged"] = True
    r = client.post("/setup/complete", json=body)
    assert r.status_code == 409 and "Model available" in r.json()["detail"]
    assert client.get("/setup/status").json()["first_run_complete"] is False
    monkeypatch.setattr(health, "check_model", lambda m: ok_check("Model available"))
    j = client.post("/setup/complete", json=body).json()
    assert j["first_run_complete"] and j["legal_acknowledged"] and j["display_name"] == "Aryan"
    assert client.get("/settings").json()["settings"]["first_run_complete"] is True


def test_complete_rejects_blank_name(client, healthy):
    r = client.post("/setup/complete", json={"display_name": "", "footer_text": "x", "legal_acknowledged": True})
    assert r.status_code == 422


# ---------------------------------------------------------------------- jobs

def test_job_endpoints(client, engine):
    j = jobs.create_job("demo", {"a": 1}, engine)
    assert client.get(f"/jobs/{j['id']}").json()["payload"] == {"a": 1}
    assert client.get("/jobs/999").status_code == 404
    assert client.post("/jobs/999/cancel").status_code == 404
    assert client.post(f"/jobs/{j['id']}/cancel").json()["state"] == "cancelled"
    assert client.get("/jobs", params={"kind": "demo"}).json()[0]["state"] == "cancelled"


# ----------------------------------------------------------------- SSE stream

def _collect(engine, last_id, n_polls=3, **kw):
    async def run():
        polls = 0

        async def disconnected():
            nonlocal polls
            polls += 1
            return polls > n_polls

        return [chunk async for chunk in events_router.event_stream(
            engine, last_id, disconnected, poll_s=0.01, **kw)]
    return asyncio.run(run())


def test_sse_stream_emits_backlog_in_order_then_stops(engine):
    a = events.emit("job", {"id": 1, "state": "running"}, engine)
    b = events.emit("job", {"id": 1, "state": "succeeded"}, engine)
    chunks = _collect(engine, 0)
    assert chunks[0].startswith("retry:")
    assert chunks[1].startswith(f"id: {a}\nevent: job\ndata: ")
    assert chunks[2].startswith(f"id: {b}\n")
    assert chunks[1].endswith("\n\n")


def test_sse_resumes_after_last_event_id(engine):
    a = events.emit("t", {"i": 1}, engine)
    b = events.emit("t", {"i": 2}, engine)
    chunks = _collect(engine, a)
    assert not any(f"id: {a}\n" in c for c in chunks)
    assert any(f"id: {b}\n" in c for c in chunks)


def test_sse_keepalive_when_idle(engine):
    chunks = _collect(engine, 0, heartbeat_s=0)
    assert any(c.startswith(": keep-alive") for c in chunks)


def test_events_endpoint_requires_token(anon):
    assert anon.get("/events").status_code == 401


# ------------------------------------------------------------------- OpenAPI

def test_openapi_export_has_no_runtime_leaks(app):
    schema = app.openapi()
    update_props = schema["components"]["schemas"]["SettingsUpdate"]["properties"]
    for forbidden in ("gmail_address", "first_run_complete", "legal_acknowledged"):
        assert forbidden not in update_props
    for name, s in schema["components"]["schemas"].items():
        if name == "GmailUpdate":  # the only request that carries the password
            continue
        for prop, spec in s.get("properties", {}).items():
            if "password" in prop:  # status flags such as password_set must be booleans
                assert spec.get("type") == "boolean", (name, prop)


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_cors_preflight_allows_every_method_the_ui_uses(anon, method):
    """The renderer is a different origin, so each method it calls needs a passing preflight. (PATCH was
    once missing, which silently broke inline edits and the lead drawer.)"""
    r = anon.options("/leads/1", headers={"Origin": ORIGIN, "Access-Control-Request-Method": method,
                                          "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == ORIGIN
    assert method in r.headers["access-control-allow-methods"]


def test_every_http_method_in_the_api_is_covered_by_cors(app):
    used = {m.upper() for ops in app.openapi()["paths"].values() for m in ops}
    allowed = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    assert used <= allowed, f"add {used - allowed} to the CORS allow_methods"


def test_resume_endpoint_requeues_only_cancelled_or_failed_runs(client, engine):
    plan = {"mode": "service", "search_queries": ["q"]}
    r = client.post("/discovery/run", json={"request_text": "a", "plan": plan}).json()
    assert client.post(f"/discovery/runs/{r['id']}/resume").status_code == 409  # still queued/active
    client.post(f"/discovery/runs/{r['id']}/cancel")
    assert client.get(f"/discovery/runs/{r['id']}").json()["state"] == "cancelled"
    again = client.post(f"/discovery/runs/{r['id']}/resume")
    assert again.status_code == 202 and again.json()["state"] == "queued" and again.json()["job_id"] != r["job_id"]
    assert client.post("/discovery/runs/999/resume").status_code in (404, 409)
