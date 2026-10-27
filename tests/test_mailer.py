import logging
import smtplib

import pytest

from backend.core import db, mailer as mm, secrets
from backend.core.ports import (MailerError, OutcomeUnknownError, OutgoingEmail, RecipientRejected, SmtpAuthError,
                                TransientSendError)
from tests.smtp_sink import Sink

PASSWORD = "abcd efgh ijkl mnop".replace(" ", "")


@pytest.fixture
def sink():
    s = Sink().start()
    yield s
    s.stop()


def make(sink, timeout=5.0, password=PASSWORD):
    return mm.SmtpMailer("me@gmail.com", password, host="127.0.0.1", port=sink.port, timeout=timeout,
                         smtp_factory=lambda: smtplib.SMTP("127.0.0.1", sink.port, timeout=timeout))


def email(**kw):
    base = dict(from_addr="me@gmail.com", from_name="Aryan Kumar", to_addr="a@acme.ai", subject="Hello there",
                body="Hi,\n.dotted line\nBye\n", message_id=mm.new_message_id("me@gmail.com"))
    base.update(kw)
    return OutgoingEmail(**base)


def test_plain_text_message_with_headers(sink):
    e = email()
    receipt = make(sink).send(e)
    assert receipt.smtp_result.startswith("250")
    m = sink.parsed()
    assert not m.is_multipart() and m.get_content_type() == "text/plain"
    assert m["Message-ID"] == e.message_id and e.message_id.startswith("<") and e.message_id.endswith("@gmail.com>")
    assert "Aryan Kumar" in m["From"] and "me@gmail.com" in m["From"]
    assert m["In-Reply-To"] is None and m["References"] is None
    assert ".dotted line" in m.get_content()  # dot-stuffing round-trips
    assert sink.messages[0][1] == ["a@acme.ai"] and sink.logins == [("me@gmail.com", PASSWORD)]
    raw = sink.messages[0][2].lower()
    assert b"<html" not in raw and b"<img" not in raw and b"text/html" not in raw  # no tracking, no HTML part


def test_followup_headers(sink):
    make(sink).send(email(in_reply_to="<first@x>", references=("<first@x>", "<second@x>")))
    m = sink.parsed()
    assert m["In-Reply-To"] == "<first@x>" and m["References"] == "<first@x> <second@x>"


def test_subject_and_name_cannot_inject_headers(sink):
    make(sink).send(email(subject="Hi\r\nBcc: evil@x.com", from_name="Me\r\nBcc: z@x.com"))
    m = sink.parsed()
    assert m["Bcc"] is None and "\n" not in m["Subject"]


def test_single_pdf_attachment(sink, tmp_path):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    make(sink).send(email(attachment_path=str(pdf)))
    m = sink.parsed()
    atts = list(m.iter_attachments())
    assert len(atts) == 1 and atts[0].get_filename() == "cv.pdf" and atts[0].get_content_type() == "application/pdf"
    assert atts[0].get_content() == b"%PDF-1.4 fake"


def test_missing_attachment_fails_before_any_connection(sink):
    with pytest.raises(MailerError) as ei:
        make(sink).send(email(attachment_path="/nonexistent/cv.pdf"))
    assert not ei.value.outcome_unknown and not sink.logins and not sink.messages


def test_login_refused_is_auth_error_without_sending(sink):
    sink.auth_ok = False
    with pytest.raises(SmtpAuthError) as ei:
        make(sink).send(email())
    assert PASSWORD not in str(ei.value) and not sink.messages


def test_check_login(sink):
    make(sink).check_login()
    assert sink.logins
    sink.auth_ok = False
    with pytest.raises(SmtpAuthError):
        make(sink).check_login()


def test_recipient_refused(sink):
    sink.rcpt_reply["a@acme.ai"] = "550 5.1.1 No such user"
    with pytest.raises(RecipientRejected):
        make(sink).send(email())
    assert not sink.messages


def test_4xx_before_data_is_transient(sink):
    sink.rcpt_reply["a@acme.ai"] = "451 4.3.0 try later"
    with pytest.raises(TransientSendError):
        make(sink).send(email())
    sink.rcpt_reply.clear()
    sink.mail_reply = "421 4.7.0 slow down"
    with pytest.raises(TransientSendError):
        make(sink).send(email())
    sink.mail_reply = "250 OK"
    sink.data_cmd_reply = "451 4.3.0 no"
    with pytest.raises(TransientSendError):
        make(sink).send(email())


def test_account_level_refusals_stop_the_run(sink):
    sink.mail_reply = "550 5.7.1 sender blocked"
    with pytest.raises(SmtpAuthError):
        make(sink).send(email())
    sink.mail_reply = "250 OK"
    sink.final_reply = "550 5.4.5 Daily user sending quota exceeded"
    with pytest.raises(mm.SendingLimitError):
        make(sink).send(email())


def test_data_stage_refusals_are_definite(sink):
    sink.final_reply = "554 5.7.1 message rejected as spam"
    with pytest.raises(RecipientRejected) as ei:
        make(sink).send(email())
    assert not ei.value.outcome_unknown
    sink.final_reply = "451 4.3.0 temporary"
    with pytest.raises(TransientSendError):
        make(sink).send(email())
    sink.final_reply = "299 weird"
    with pytest.raises(OutcomeUnknownError):
        make(sink).send(email())


def test_drop_after_data_is_outcome_unknown(sink):
    sink.drop_after_data = True
    with pytest.raises(OutcomeUnknownError) as ei:
        make(sink).send(email())
    assert ei.value.outcome_unknown


def test_timeout_after_data_is_outcome_unknown(sink):
    sink.hang_after_data = 1.5
    with pytest.raises(OutcomeUnknownError):
        make(sink, timeout=0.4).send(email())


def test_connection_refused_is_transient():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    m = mm.SmtpMailer("me@gmail.com", PASSWORD, smtp_factory=lambda: smtplib.SMTP("127.0.0.1", port, timeout=1))
    with pytest.raises(TransientSendError):
        m.send(email())


def test_ssl_factory_targets_gmail(monkeypatch):
    seen = {}

    class Boom(Exception):
        pass

    def fake_ssl(host, port, timeout, context):
        seen.update(host=host, port=port, ctx=context)
        raise OSError("blocked in test")

    monkeypatch.setattr(smtplib, "SMTP_SSL", fake_ssl)
    with pytest.raises(TransientSendError):
        mm.SmtpMailer("me@gmail.com", PASSWORD).send(email())
    assert (seen["host"], seen["port"]) == ("smtp.gmail.com", 465) and seen["ctx"].verify_mode.name == "CERT_REQUIRED"


def test_from_settings_reads_keychain(engine, memory_keyring):
    with pytest.raises(SmtpAuthError):
        mm.SmtpMailer.from_settings(engine)  # nothing configured
    s = db.load_settings(engine).model_copy(update={"gmail_address": "me@gmail.com"})
    db.save_settings(s, engine)
    with pytest.raises(SmtpAuthError):
        mm.SmtpMailer.from_settings(engine)  # address but no password
    secrets.set_app_password("me@gmail.com", "abcd efgh ijkl mnop")
    m = mm.SmtpMailer.from_settings(engine)
    assert m.address == "me@gmail.com" and PASSWORD not in repr(m)
    m.close()


def test_app_password_never_appears_in_logs(sink, caplog):
    caplog.set_level(logging.DEBUG)
    smtplib.SMTP.debuglevel = 0
    sink.auth_ok = False
    with pytest.raises(SmtpAuthError) as ei:
        make(sink).send(email())
    sink.auth_ok = True
    make(sink).send(email())
    sink.rcpt_reply["a@acme.ai"] = "550 nope"
    with pytest.raises(RecipientRejected) as ei2:
        make(sink).send(email())
    blob = caplog.text + str(ei.value) + repr(ei.value.__cause__) + str(ei2.value) + repr(make(sink))
    assert PASSWORD not in blob
