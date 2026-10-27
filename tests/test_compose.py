"""Templates, profile fields and rendering (composer + their API)."""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.core import composer, db
from backend.core.models import AppSettings
from tests import factories as f

TOKEN = "t" * 32


@pytest.fixture
def client(engine, memory_keyring):
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=engine)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


def tpl(**kw):
    base = dict(mode="internship", name="T", subject="Hi {{company}}", body="Hi {{first_name}},\n\n{{custom_opener}}\n\n{{my_name}}")
    return base | kw


def test_starters_seeded_once_and_short(client):
    first = client.get("/templates").json()
    assert [t["mode"] for t in first] == ["internship", "investor", "service"]
    assert all(t["is_starter"] for t in first)
    for t in first:
        # body text without the opener (<= 35 words) stays under 120 words even with profile text filled in
        assert len(t["body"].replace("{{custom_opener}}", "").split()) + 35 <= 120, t["name"]
        assert "{{custom_opener}}" in t["body"] and "custom_opener" in t["merge_fields"]
        assert t["followup_1_body"] and "<" not in t["body"]  # plain text
    client.delete(f"/templates/{first[0]['id']}")
    assert len(client.get("/templates").json()) == 2  # not re-seeded after deletion


def test_template_crud_and_merge_fields(client):
    r = client.post("/templates", json=tpl())
    assert r.status_code == 201
    t = r.json()
    assert t["merge_fields"] == ["first_name", "company", "my_name", "custom_opener"]
    up = client.put(f"/templates/{t['id']}", json=tpl(name="Renamed", body="{{my_pitch}} {{custom_opener}}"))
    assert up.json()["name"] == "Renamed" and "my_pitch" in up.json()["merge_fields"]
    assert client.put("/templates/9999", json=tpl()).status_code == 404
    assert client.delete(f"/templates/{t['id']}").status_code == 204
    assert client.delete(f"/templates/{t['id']}").status_code == 404


def test_syntax_error_is_422_with_line(client):
    r = client.post("/templates", json=tpl(body="Hi {{first_name"))
    assert r.status_code == 422 and "line" in r.json()["detail"]


def test_template_in_use_cannot_be_deleted(client, engine):
    t = client.post("/templates", json=tpl()).json()
    f.campaign(engine, template_id=t["id"])
    assert client.delete(f"/templates/{t['id']}").status_code == 409


def test_profile_crud_and_reserved_keys(client):
    assert {x["key"] for x in client.get("/profile").json()["fields"]} == {"my_summary", "my_pitch", "my_traction"}
    ok = client.put("/profile", json={"fields": [{"key": "my_pitch", "label": "Pitch", "value": "I build things."}]})
    assert ok.status_code == 200 and ok.json()["fields"][0]["value"] == "I build things."
    assert "custom_opener" in ok.json()["reserved"]
    assert client.put("/profile", json={"fields": [{"key": "company", "label": "x", "value": ""}]}).status_code == 422
    dup = [{"key": "a", "label": "A", "value": ""}] * 2
    assert client.put("/profile", json={"fields": dup}).status_code == 422
    assert client.put("/profile", json={"fields": [{"key": "Bad Key", "label": "x", "value": ""}]}).status_code == 422


def test_preview_footer_span_and_missing(client, engine):
    s = AppSettings(display_name="Sam Ng")
    db.save_settings(s, engine)
    lead = f.lead(engine, name="Priya Shah", company="Acme", role="CTO")
    r = client.post("/templates/preview", json={"template": tpl(body="Hi {{first_name}} at {{company}},\n\n{{custom_opener}}\n\n{{my_pitch}}"), "lead_id": lead})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["body"].startswith("Hi Priya at Acme,")
    assert p["missing_fields"] == ["my_pitch"]  # profile value is blank
    seg = p["body"][p["opener_span"]["start"]:p["opener_span"]["end"]]
    assert seg.startswith("(A short opener") and seg.endswith("here.)")
    assert p["final_text"].startswith(p["body"]) and "Sam Ng" in p["final_text"] and "reply 'stop'" in p["final_text"]
    assert p["subject"] == "Hi Acme" and p["word_count"] == len(p["body"].split())


def test_preview_sample_lead_and_errors(client):
    assert client.post("/templates/preview", json={"template": tpl()}).json()["body"].startswith("Hi Alex,")
    assert client.post("/templates/preview", json={"template": tpl(), "lead_id": 999}).status_code == 404
    bad = tpl(body="{% for %}")
    assert client.post("/templates/preview", json={"template": bad}).status_code == 422


def test_render_is_sandboxed_and_never_executes_values():
    t = dict(subject="s", body="{{ ''.__class__.__mro__ }} {{custom_opener}}")
    with pytest.raises(composer.TemplateError):
        composer.render(t, {}, "x")
    t2 = dict(subject="s", body="{{company}}")
    out = composer.render(t2, {"company": "{{ 7*7 }}"}, "o")  # profile/lead text is data, not template
    assert out.body == "{{ 7*7 }}"


def test_blank_first_name_uses_there_and_blank_lines_collapse():
    t = dict(subject="Hi {{first_name}}", body="Hi {{first_name}},\n\n{{my_pitch}}\n\n\n{{custom_opener}}")
    r = composer.render(t, {"first_name": "", "my_pitch": ""}, "Opener here.")
    assert r.subject == "Hi there" and "\n\n\n" not in r.body and r.missing == ["my_pitch"]
    assert r.body[r.opener_span[0]:r.opener_span[1]] == "Opener here."


def test_any_exception_in_a_template_becomes_a_template_error_and_a_422(client):
    for body in ("{{ 1 / 0 }}", "{{ 'a' + 1 }}", "{{ range(3) + 1 }}"):
        with pytest.raises(composer.TemplateError):
            composer.render(dict(subject="s", body=body), {"first_name": "A"}, "o")
    r = client.post("/templates/preview", json={"template": tpl(body="Hi {{ 1 / 0 }} {{custom_opener}}")})
    assert r.status_code == 422 and "could not be rendered" in r.json()["detail"]
