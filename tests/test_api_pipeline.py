import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.core import db, exports, jobs, leads, llm, validator
from backend.core.extractor import LeadDraft
from backend.core.models import EmailConfidence
from backend.core.planner import Plan

TOKEN = "t" * 32
REAL_MX = validator.MxChecker


@pytest.fixture
def client(engine, memory_keyring):
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=engine)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


def plan_body(**kw):
    return {"mode": "internship", "target_roles": ["founder"], "industries": ["AI"], "regions": ["Bangalore"],
            "keywords": [], "search_queries": ["ai startup bangalore careers", "top ai startups in bangalore"], **kw}


def D(email, name="", conf=EmailConfidence.published_personal, company="Acme"):
    return LeadDraft(email=email, name=name, company=company, domain="acme.ai", source_url="https://acme.ai/team",
                     context_snippet="ctx", email_confidence=conf)


# ---- discovery ------------------------------------------------------------------------------

def test_plan_endpoint_llm_and_fallback(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: Plan.model_validate(plan_body(mode="investor")))
    j = client.post("/discovery/plan", json={"request": "AI investors in India"}).json()
    assert j["source"] == "llm" and j["plan"]["mode"] == "investor" and 6 <= len(j["plan"]["search_queries"]) <= 12
    assert client.post("/discovery/plan", json={"request": "x", "mode": "service"}).json()["plan"]["mode"] == "service"

    def down(*a, **k):
        raise llm.LlmError("refused")

    monkeypatch.setattr(llm, "generate_json", down)
    j = client.post("/discovery/plan", json={"request": "Founders of D2C brands who need a website redesign"}).json()
    assert j["source"] == "fallback" and j["plan"]["mode"] == "service" and "not running" in j["note"]


@pytest.mark.parametrize("body", [{}, {"request": ""}, {"request": "x" * 2001}, {"request": "ok", "mode": "bogus"}, {"request": "ok", "extra": 1}])
def test_plan_validation(client, body):
    assert client.post("/discovery/plan", json=body).status_code == 422


def test_start_run_creates_run_and_job_and_blocks_a_second(client, engine):
    r = client.post("/discovery/run", json={"request_text": "AI interns", "plan": plan_body(), "options": {"expand_lists": False, "max_domains": 5}})
    assert r.status_code == 202
    run = r.json()
    assert run["state"] == "queued" and run["mode"] == "internship" and run["job_id"]
    job = jobs.get_job(run["job_id"], engine)
    assert job["kind"] == "discovery_run" and job["payload"]["run_id"] == run["id"]
    assert job["payload"]["options"] == {"recrawl": False, "use_overpass": False, "expand_lists": False, "max_domains": 5}
    again = client.post("/discovery/run", json={"request_text": "again", "plan": plan_body()})
    assert again.status_code == 409 and "already running" in again.json()["detail"]


def test_start_run_validation(client):
    assert client.post("/discovery/run", json={"request_text": "x", "plan": plan_body(search_queries=[])}).status_code == 422
    assert client.post("/discovery/run", json={"request_text": "x", "plan": plan_body(mode="bogus")}).status_code == 422
    assert client.post("/discovery/run", json={"request_text": "x", "plan": plan_body(), "options": {"max_domains": 0}}).status_code == 422
    assert client.post("/discovery/run", json={"request_text": "x", "plan": plan_body(), "options": {"nope": 1}}).status_code == 422


def test_runs_listing_detail_and_cancel(client, engine):
    run = client.post("/discovery/run", json={"request_text": "AI interns", "plan": plan_body()}).json()
    assert [r["id"] for r in client.get("/discovery/runs").json()] == [run["id"]]
    d = client.get(f"/discovery/runs/{run['id']}").json()
    assert d["domains"] == [] and d["log"] == [] and d["plan"]["industries"] == ["AI"]
    assert client.get("/discovery/runs/999").status_code == 404 and client.post("/discovery/runs/999/cancel").status_code == 404
    c = client.post(f"/discovery/runs/{run['id']}/cancel").json()
    assert jobs.get_job(run["job_id"], engine)["state"] == "cancelled" and c["id"] == run["id"]
    # once cancelled, a new search may start
    assert client.post("/discovery/run", json={"request_text": "next", "plan": plan_body()}).status_code == 202


# ---- leads ----------------------------------------------------------------------------------

def seed(engine):
    leads.ingest([D("a@acme.ai", "Ann", company="Alpha"), D("b@acme.ai", "Bob", EmailConfidence.published_generic, "Beta"),
                  D("c@acme.ai", "Cy", EmailConfidence.guessed, "Gamma")], run_id=None, mode="service", engine=engine)


def test_list_leads_filters_sort_and_pagination(client, engine):
    seed(engine)
    j = client.get("/leads").json()
    assert j["total"] == 3 and len(j["items"]) == 3 and "context_snippet" not in j["items"][0]
    assert client.get("/leads", params={"confidence": ["guessed", "published_generic"]}).json()["total"] == 2
    assert client.get("/leads", params={"q": "alpha"}).json()["items"][0]["email"] == "a@acme.ai"
    assert client.get("/leads", params={"sort": "company:desc"}).json()["items"][0]["company"] == "Gamma"
    assert client.get("/leads", params={"limit": 1, "offset": 1, "sort": "company:asc"}).json()["items"][0]["company"] == "Beta"
    assert client.get("/leads", params={"status": ["new", "sent"], "mode": "service"}).json()["total"] == 3
    assert client.get("/leads", params={"run_id": 99}).json()["total"] == 0


@pytest.mark.parametrize("params", [{"limit": 5001}, {"limit": 0}, {"offset": -1}, {"status": "bogus"}, {"confidence": "maybe"}, {"mode": "x"}])
def test_list_leads_validation(client, params):
    assert client.get("/leads", params=params).status_code == 422


def test_sort_injection_is_harmless(client, engine):
    seed(engine)
    assert client.get("/leads", params={"sort": "name; DROP TABLE leads;--:asc"}).json()["total"] == 3


def test_get_and_patch_lead(client, engine):
    seed(engine)
    lid = client.get("/leads").json()["items"][0]["id"]
    d = client.get(f"/leads/{lid}").json()
    assert d["context_snippet"] == "ctx" and d["timeline"] == [] and d["run"] is None
    p = client.patch(f"/leads/{lid}", json={"name": "New", "status": "closed", "notes": "hi"}).json()
    assert (p["name"], p["status"], p["notes"]) == ("New", "closed", "hi")
    for bad in ({"email": "x@y.com"}, {"status": "bogus"}, {"name": "x" * 201}, {"source_url": "u"}):
        assert client.patch(f"/leads/{lid}", json=bad).status_code == 422
    assert client.get(f"/leads/{lid}").json()["email"] != "x@y.com"
    assert client.patch("/leads/9999", json={"name": "x"}).status_code == 404 and client.get("/leads/9999").status_code == 404


def test_bulk_endpoint(client, engine):
    seed(engine)
    ids = [x["id"] for x in client.get("/leads").json()["items"]]
    assert client.post("/leads/bulk", json={"action": "delete"}).status_code == 422
    assert client.post("/leads/bulk", json={"action": "set_status", "ids": ids}).status_code == 422
    assert client.post("/leads/bulk", json={"action": "explode", "ids": ids}).status_code == 422
    assert client.post("/leads/bulk", json={"action": "set_status", "status": "approved", "ids": ids[:2]}).json() == {"affected": 2, "skipped": 0}
    r = client.post("/leads/bulk", json={"action": "suppress", "filter": {"status": ["approved"]}}).json()
    assert r["affected"] == 2 and db.is_suppressed("c@acme.ai", engine) and db.is_suppressed("b@acme.ai", engine) and not db.is_suppressed("a@acme.ai", engine)
    assert all(e["removable"] for e in client.get("/suppression").json())  # manual: the user may undo this
    assert client.post("/leads/bulk", json={"action": "delete", "ids": ids}).json()["affected"] == 3
    assert client.get("/leads").json()["total"] == 0


CSV = "name,email,company\nAnn,ann@good.ai,Good\nBad,nope,\nNo,x@nomx.ai,\n"


@pytest.fixture
def mx(monkeypatch):
    monkeypatch.setattr(exports.validator, "MxChecker", lambda: REAL_MX(lookup=lambda d: d != "nomx.ai"))


def test_import_csv_dry_run_then_commit(client, engine, mx):
    h = {"Content-Type": "text/csv"}
    r = client.post("/leads/import-csv", params={"dry_run": True}, content=CSV, headers=h).json()
    assert (r["total"], r["valid"], r["inserted"], r["invalid"], r["no_mx"]) == (3, 1, 0, 1, 1)
    assert {i["reason"] for i in r["issues"]} == {"Invalid or unusable email address", "Domain has no mail server (MX record)"}
    assert client.get("/leads").json()["total"] == 0
    r = client.post("/leads/import-csv", content=CSV, headers=h).json()
    assert r["inserted"] == 1 and client.get("/leads").json()["items"][0]["email"] == "ann@good.ai"


def test_import_csv_rejections(client, mx):
    assert client.post("/leads/import-csv", content="name\nx\n").status_code == 422
    assert client.post("/leads/import-csv", content=b"\xff\xfe\x00bad").status_code == 422
    assert client.post("/leads/import-csv", content="email\n" + "a" * 5_100_000).status_code == 413


def test_export_csv_endpoint(client, engine):
    leads.ingest([D("a@acme.ai", "=EVIL()", company="Alpha"), D("b@acme.ai", "Bob", EmailConfidence.guessed, "Beta")], run_id=None, mode=None, engine=engine)
    r = client.get("/leads/export.csv", params={"confidence": "guessed"})
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    assert "b@acme.ai" in r.text and "a@acme.ai" not in r.text
    assert "'=EVIL()" in client.get("/leads/export.csv").text
    assert r.text.splitlines()[0] == "name,email,role,company,domain,source_url,email_confidence,mode,notes"


def test_openapi_has_typed_pipeline_endpoints(client):
    schema = client.app.openapi()
    for path in ("/discovery/plan", "/discovery/run", "/discovery/runs/{run_id}", "/leads", "/leads/bulk", "/leads/import-csv", "/leads/export.csv"):
        assert path in schema["paths"]
    assert "ImportReportOut" in schema["components"]["schemas"]
