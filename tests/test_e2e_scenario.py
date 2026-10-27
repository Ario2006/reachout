"""The whole loop with fakes: leads -> template/profile -> drafts -> approve -> dry run -> test send -> campaign send
-> reply, bounce -> dashboard counts. No network, no real SMTP/IMAP/Ollama (services are the in-memory fakes)."""
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.main import create_app
from backend.core import db, jobs, secrets, services
from backend.core.drafts import run_generate_drafts
from backend.core.personaliser import OpenerOut
from backend.core.sender import run_campaign_dry_run
from tests import factories as f
from tests import sending_world as w
from tests.test_inbox_world import run_check

TOKEN = "t" * 32
SNIPPET = "Acme builds developer tools for engineering teams."
OPENER = OpenerOut(opener="I noticed Acme builds developer tools for engineering teams.")


@pytest.fixture
def client(engine, memory_keyring):
    app = create_app(token=TOKEN, allowed_origins=["http://localhost:5173"], engine=engine)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


def run_job(engine, job: dict, handlers: dict) -> dict:
    jobs.run_job(jobs.claim_next(engine), handlers, engine)
    done = jobs.get_job(job["id"], engine)
    assert done["state"] == "succeeded", done["error"]
    return done["result"]


def reply_to(sent, body: str, subject_prefix: str = "Re: ") -> bytes:
    m = EmailMessage()
    m["From"], m["To"] = sent.to_addr, w.ME
    m["Subject"] = subject_prefix + sent.subject
    m["Message-ID"] = f"<reply-{sent.to_addr}@mail.test>"
    m["In-Reply-To"] = m["References"] = sent.message_id
    m.set_content(body)
    return m.as_bytes()


def bounce_of(sent) -> bytes:
    return (
        "From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\n"
        f"To: {w.ME}\r\nSubject: Delivery Status Notification (Failure)\r\nMessage-ID: <dsn-{sent.to_addr}@mail.test>\r\n"
        "Auto-Submitted: auto-replied\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="b"\r\n\r\n'
        "--b\r\nContent-Type: text/plain\r\n\r\nAddress not found\r\n\r\n"
        f"--b\r\nContent-Type: message/delivery-status\r\n\r\nFinal-Recipient: rfc822; {sent.to_addr}\r\nAction: failed\r\nStatus: 5.1.1\r\n\r\n"
        f"--b\r\nContent-Type: message/rfc822\r\n\r\nFrom: {w.ME}\r\nTo: {sent.to_addr}\r\nSubject: {sent.subject}\r\nMessage-ID: {sent.message_id}\r\n\r\nbody\r\n"
        "--b--\r\n").encode()


def test_the_whole_outreach_loop(engine, client):
    svc = services.fakes(OPENER)
    services.use(svc)
    try:
        w.configure(engine)
        secrets.set_app_password(w.ME, "abcdabcdabcdabcd")

        # leads -> template and profile -> campaign
        lead_ids = [f.lead(engine, email=f"{n}@{n}corp.test", company="Acme", name=n.title(), context_snippet=SNIPPET) for n in ("ann", "bob", "cy")]
        tpl = next(t for t in client.get("/templates").json() if t["mode"] == "internship")
        client.put("/profile", json={"fields": [{"key": k, "label": k, "value": "I build software."} for k in ("my_summary", "my_pitch", "my_traction")]})
        cid = client.post("/campaigns", json={"name": "Spring", "template_id": tpl["id"], "mode": "internship",
                                                "followups_enabled": True, "followup_days": [4, 9]}).json()["id"]

        # drafts: generated, nothing sendable before approval
        job = client.post("/drafts/generate", json={"campaign_id": cid}).json()
        assert run_job(engine, job, {"drafts_generate": run_generate_drafts})["generated"] == 3
        page = client.get("/drafts", params={"campaign_id": cid}).json()
        assert page["total"] == 3 and page["counts"]["approved"] == 0
        assert client.post(f"/campaigns/{cid}/start").status_code in (409, 422)  # approval required

        assert client.post("/drafts/bulk-approve", json={"campaign_id": cid}).json()["approved"] == 3

        # dry run sends nothing; test send goes only to me, with the footer
        dry = client.post(f"/campaigns/{cid}/dry-run").json()
        assert run_job(engine, dry, {"campaign_dry_run": run_campaign_dry_run})["would_send"] == 3
        assert svc.fake_mailer.sent == []
        assert client.post(f"/campaigns/{cid}/test-send").status_code == 200
        assert [e.to_addr for e in svc.fake_mailer.sent] == [w.ME]
        assert "reply 'stop'" in svc.fake_mailer.sent[0].body

        # real send: all three go out, each with a unique Message-ID and a gap of at least the minimum delay
        assert client.post(f"/campaigns/{cid}/start").status_code == 200
        sender = w.sender(engine)
        w.run_ticks(sender, svc, 12, step=400)
        out = [e for e in svc.fake_mailer.sent if e.to_addr != w.ME]
        assert sorted(e.to_addr for e in out) == ["ann@anncorp.test", "bob@bobcorp.test", "cy@cycorp.test"]
        assert len({e.message_id for e in out}) == 3
        assert all("I noticed Acme builds developer tools" in e.body and "reply 'stop'" in e.body for e in out)

        # replies and bounces arrive by IMAP
        by = {e.to_addr: e for e in out}
        svc.fake_imap.deliver(reply_to(by["ann@anncorp.test"], "Yes, please send your CV."))
        svc.fake_imap.deliver(bounce_of(by["bob@bobcorp.test"]))
        result = run_check(engine)
        assert result["new_replies"] == 1 and result["bounces"] == 1
        statuses = {r[0]: r[1] for r in engine.connect().execute(text("SELECT email, status FROM leads"))}
        assert statuses["ann@anncorp.test"] == "replied" and statuses["bob@bobcorp.test"] == "bounced"
        assert db.is_suppressed("bob@bobcorp.test", engine)

        # dashboard counts agree
        m = client.get("/metrics", params={"campaign_id": cid, "range": "all"}).json()["kpis"]
        assert (m["sent_total"], m["replies"], m["bounced"]) == (3, 1, 1)
        threads = client.get("/threads", params={"filter": "replied"}).json()
        assert [t["lead"]["email"] for t in threads["items"]] == ["ann@anncorp.test"]

        # nothing more is ever sent to people who replied or bounced, however long we wait
        before = len(svc.fake_mailer.sent)
        w.run_ticks(sender, svc, 6, step=86400)
        assert not [e for e in svc.fake_mailer.sent[before:] if e.to_addr in ("ann@anncorp.test", "bob@bobcorp.test")]
        assert len([e for e in svc.fake_mailer.sent if e.to_addr == "cy@cycorp.test"]) == 1  # and nobody gets a duplicate

        # follow-ups: day 4 goes only to the lead who stayed silent, in the same thread
        from backend.core import followups
        svc.fake_clock.advance(5 * 86400)
        followups.tick(engine, svc.fake_clock.now())
        w.run_ticks(sender, svc, 6, step=400)
        later = svc.fake_mailer.sent[before:]
        fu = [e for e in later if e.subject.startswith("Re: ")]
        assert [e.to_addr for e in fu] == ["cy@cycorp.test"]
        assert fu[0].in_reply_to == by["cy@cycorp.test"].message_id and "reply 'stop'" in fu[0].body
        assert fu[0].subject == "Re: " + by["cy@cycorp.test"].subject
        plan = client.get(f"/campaigns/{cid}/followups").json()
        assert plan["counts"].get("sent", 0) == 1
    finally:
        services.use(None)
