"""Phase 0 contracts: migration 005 guarantees, footer rule, ports/fakes, campaign CRUD, event shapes, API schema."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.api.main import create_app
from backend.core import event_types, events, services
from backend.core.fakes import FakeClock, FakeImap, FakeLlm, FakeMailer
from backend.core.footer import DEFAULT_FOOTER, ensure_footer, footer_text
from backend.core.models import AppSettings, Mode
from backend.core.ports import OutgoingEmail, RecipientRejected, SmtpAuthError
from tests import factories as f

TOKEN = "t" * 32


@pytest.fixture
def client(engine, memory_keyring):
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=engine)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


# ------------------------------------------------------------------ migration 005
def _row(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).first()


def test_editing_an_approved_draft_withdraws_the_approval(engine):
    cid, lid = f.campaign(engine), f.lead(engine)
    did = f.draft(engine, cid, lid, "approved")
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET body='changed' WHERE id=:i"), {"i": did})
    assert tuple(_row(engine, "SELECT status, approved, approved_at FROM drafts WHERE id=:i", i=did)) == ("pending", 0, None)


def test_changing_only_status_keeps_the_approval_and_flag_is_consistent(engine):
    cid, lid = f.campaign(engine), f.lead(engine)
    did = f.draft(engine, cid, lid, "pending")
    with engine.begin() as c:
        c.execute(text("UPDATE drafts SET status='approved', approved=1 WHERE id=:i"), {"i": did})
    assert _row(engine, "SELECT status FROM drafts WHERE id=:i", i=did)[0] == "approved"
    with pytest.raises(IntegrityError), engine.begin() as c:  # approved flag may never disagree with status
        c.execute(text("UPDATE drafts SET approved=0 WHERE id=:i"), {"i": did})


def test_one_draft_per_lead_campaign_and_kind(engine):
    cid, lid = f.campaign(engine), f.lead(engine)
    f.draft(engine, cid, lid)
    f.draft(engine, cid, lid, kind="followup_1")
    with pytest.raises(IntegrityError):
        f.draft(engine, cid, lid)


def test_an_incoming_email_is_recorded_once(engine):
    lid = f.lead(engine)
    ins = "INSERT INTO events (lead_id, type, imap_message_id) VALUES (:l, 'reply', '<a@x>')"
    with engine.begin() as c:
        c.execute(text(ins), {"l": lid})
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text(ins), {"l": lid})


@pytest.mark.parametrize("key", ["Name", "a-b", "1x", "a b", ""])
def test_profile_keys_must_be_merge_safe(engine, key):
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text("INSERT INTO profile_fields (key, label) VALUES (:k, 'x')"), {"k": key})


# ------------------------------------------------------------------ footer
S = AppSettings(display_name="Aryan K", footer_text=DEFAULT_FOOTER)


def test_footer_carries_name_and_opt_out_and_is_idempotent():
    once = ensure_footer("Hello", S, Mode.service)
    assert "Aryan K" in once and "reply 'stop'" in once
    assert ensure_footer(once, S, Mode.service) == once


@pytest.mark.parametrize("mode", [Mode.service, Mode.investor, None])
def test_footer_cannot_be_cleared_for_service_investor_or_unknown_mode(mode):
    assert DEFAULT_FOOTER in footer_text(S.model_copy(update={"footer_text": "  "}), mode)


def test_internship_may_clear_the_footer():
    assert footer_text(S.model_copy(update={"footer_text": ""}), Mode.internship) == ""


@given(st.text(max_size=300))
def test_footer_application_is_idempotent_for_any_body(body):
    once = ensure_footer(body, S, Mode.investor)
    assert ensure_footer(once, S, Mode.investor) == once and DEFAULT_FOOTER in once


# ------------------------------------------------------------------ ports and fakes
def _mail(to="a@b.test", mid="<1@x>"):
    return OutgoingEmail("me@x.test", "Me", to, "S", "B", mid)


def test_fake_mailer_records_and_can_fail_per_recipient():
    m = FakeMailer()
    m.fail_with["bad@b.test"] = RecipientRejected("550")
    assert m.send(_mail()).smtp_result.startswith("250")
    with pytest.raises(RecipientRejected):
        m.send(_mail("bad@b.test"))
    m.login_error = SmtpAuthError("no")
    with pytest.raises(SmtpAuthError):
        m.check_login()
    assert [e.to_addr for e in m.sent] == ["a@b.test"]


def test_fake_imap_serves_only_newer_uids_in_order():
    i = FakeImap()
    assert (i.deliver("a"), i.deliver("b")) == (1, 2)
    assert [m.uid for m in i.fetch_new("INBOX", 1).messages] == [2]
    assert i.fetch_new("INBOX", 2).messages == []


def test_fake_llm_replays_in_order_then_repeats_the_last():
    l = FakeLlm("one", RuntimeError("down"), "three")
    assert l.generate_json(str, "s", "u") == "one"
    with pytest.raises(RuntimeError):
        l.generate_json(str, "s", "u")
    assert [l.generate_json(str, "s", "u") for _ in range(2)] == ["three", "three"]


def test_fake_clock_sleep_advances_time_without_waiting():
    c = FakeClock()
    t0 = c.now()
    c.sleep(120)
    assert c.now() == t0 + 120 and c.slept == [120]


def test_services_can_be_swapped_for_fakes():
    fk = services.fakes()
    services.use(fk)
    try:
        assert services.get_services().mailer() is fk.fake_mailer
    finally:
        services.use(None)


# ------------------------------------------------------------------ SSE shapes
def test_publish_validates_payload_type_and_writes_the_event(engine):
    before = events.latest_id(engine)
    event_types.publish("draft", event_types.DraftEvent(campaign_id=1, action="bulk"), engine)
    assert events.read_since(before, engine=engine)[0]["payload"] == {"campaign_id": 1, "draft_id": None, "action": "bulk"}
    with pytest.raises(TypeError):
        event_types.publish("draft", event_types.InboxEvent(lead_id=1, event_id=1, type="reply"), engine)


# ------------------------------------------------------------------ campaign CRUD
def test_campaign_crud_roundtrip_and_defaults(client):
    r = client.post("/campaigns", json={"name": "Spring", "mode": "internship", "followup_days": [3, 8]})
    assert r.status_code == 201
    c = r.json()
    assert c["state"] == "draft" and c["daily_cap"] == 25 and c["followup_days"] == [3, 8] and c["counts"]["queued"] == 0
    c2 = client.put(f"/campaigns/{c['id']}", json={"name": "Spring 2", "daily_cap": 30}).json()
    assert c2["name"] == "Spring 2" and any("Daily cap" in w for w in c2["warnings"])
    assert client.get("/campaigns").json()[0]["id"] == c["id"]
    assert client.delete(f"/campaigns/{c['id']}").status_code == 204
    assert client.get(f"/campaigns/{c['id']}").status_code == 404


def test_campaign_cap_cannot_exceed_the_hard_ceiling(client):
    assert client.post("/campaigns", json={"name": "x", "daily_cap": 101}).status_code == 422


def test_running_campaign_settings_are_locked_and_history_is_not_deletable(client, engine):
    cid = client.post("/campaigns", json={"name": "x"}).json()["id"]
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET state='running' WHERE id=:i"), {"i": cid})
    assert client.put(f"/campaigns/{cid}", json={"name": "y"}).status_code == 409
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET state='draft' WHERE id=:i"), {"i": cid})
    f.lead(engine)
    with engine.begin() as c:
        c.execute(text("INSERT INTO messages (lead_id, campaign_id, kind) VALUES (1, :c, 'initial')"), {"c": cid})
    assert client.delete(f"/campaigns/{cid}").status_code == 409


def test_large_attachment_gets_a_warning(client, tmp_path):
    big = tmp_path / "cv.pdf"
    big.write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    c = client.post("/campaigns", json={"name": "x", "attachment_path": str(big)}).json()
    assert any("2 MB" in w for w in c["warnings"])


# ------------------------------------------------------------------ stubs and schema
def test_unbuilt_endpoints_answer_501_not_500(client):
    for method, path in [("get", "/templates"), ("get", "/drafts?campaign_id=1"), ("get", "/threads"), ("get", "/metrics"),
                         ("post", "/campaigns/1/start"), ("get", "/sending/status"), ("post", "/inbox/check-now")]:
        assert getattr(client, method)(path).status_code == 501, path


def test_committed_openapi_matches_the_code():
    """The frontend client is generated from this file; a drift means someone forgot `npm run gen:api`."""
    committed = Path(__file__).resolve().parent.parent / "desktop/src/lib/api/openapi.json"
    live = create_app(token="x" * 32, engine=object()).openapi()
    assert json.loads(committed.read_text()) == json.loads(json.dumps(live, sort_keys=True))
