"""Parsing and matching of incoming email, and the read-only Gmail client (all against .eml fixtures and fakes)."""
import imaplib

import pytest
from hypothesis import given, settings, strategies as st

from backend.core import db, tracker
from backend.core.ports import ImapAuthError
from backend.core.tracker import (GmailImap, ImapTransientError, detect_opt_out, extract_reply_text, normalize_message_id,
                                  normalize_subject, parse_email, parse_id_list)
from tests.test_inbox_world import ME, configure, eml, sent_to


def kind_of(name: str) -> str:
    return parse_email(eml(name), ME).kind


# ------------------------------------------------------------------ classification
@pytest.mark.parametrize("name,kind", [
    ("reply_threaded", "reply"), ("reply_no_headers", "reply"), ("reply_html_only", "reply"),
    ("reply_latin1_qp", "reply"), ("reply_unknown_charset", "reply"), ("reply_gmail_wrapped_quote", "reply"),
    ("reply_interested_quoted_stop", "reply"), ("notauto_autosubmitted_no", "reply"), ("optout_late_mention", "reply"),
    ("autoreply_ooo", "auto_reply"), ("autoreply_outlook_subject_only", "auto_reply"), ("autoreply_x_autoreply", "auto_reply"),
    ("autoreply_precedence_bulk", "auto_reply"), ("autoreply_with_unsubscribe", "auto_reply"),
    ("optout_stop", "opt_out"), ("optout_unsubscribe", "opt_out"), ("optout_remove_me", "opt_out"),
    ("optout_please_stop_emailing", "opt_out"),
    ("bounce_hard_511", "bounce"), ("bounce_soft_422", "bounce"), ("bounce_delayed", "bounce"),
    ("bounce_postfix_plain", "bounce"), ("bounce_exchange_no_status", "bounce"),
    ("from_self", "ignore"), ("mdn_read_receipt", "ignore"),
])
def test_classification(name, kind):
    assert kind_of(name) == kind


def test_auto_reply_is_never_a_reply_or_opt_out():
    # the auto-reply fixture also says "unsubscribe": still an auto_reply
    p = parse_email(eml("autoreply_with_unsubscribe"), ME)
    assert p.kind == "auto_reply"


def test_quoted_footer_does_not_trigger_opt_out():
    p = parse_email(eml("reply_interested_quoted_stop"), ME)
    assert p.kind == "reply" and "stop" not in p.text.lower()
    assert parse_email(eml("reply_gmail_wrapped_quote"), ME).text == "Yes please."


def test_reply_text_decoding():
    assert "Grüße aus München" in parse_email(eml("reply_latin1_qp"), ME).text
    assert "Please send me your CV" in parse_email(eml("reply_html_only"), ME).text
    assert "color:red" not in parse_email(eml("reply_html_only"), ME).text
    p = parse_email(eml("reply_latin1_qp"), ME)
    assert "Praktikum bei Latin (Straße)" in p.subject and p.from_name == "Jürgen Müller"


def test_malformed_input_never_raises():
    assert parse_email(eml("malformed_no_headers"), ME).kind in {"reply", "ignore"}
    for junk in (b"", b"\x00\xff" * 50, b"From: \nSubject\n\n", b"Content-Type: multipart/report\n\n--x"):
        parse_email(junk, ME)


def test_headers_and_dates():
    p = parse_email(eml("reply_threaded"), ME)
    assert p.in_reply_to == ("sent-acme-1@reachout.test",) and p.references == ("sent-acme-1@reachout.test",)
    assert p.message_id == "in-acme-1@mail.example.test" and p.occurred_at == "2026-10-27T05:45:03Z"
    assert parse_email(eml("malformed_bad_date_no_id"), ME).occurred_at is None
    assert parse_email(eml("malformed_bad_date_no_id"), ME).message_id is None


# ------------------------------------------------------------------ bounces
def test_hard_bounce_511():
    p = parse_email(eml("bounce_hard_511"), ME)
    assert p.hard_bounce and p.status_code == "5.1.1" and not p.soft
    assert p.failed_recipients == ("nobody@gone.test",) and p.original_ids == ("sent-gone-1@reachout.test",)
    assert "5.1.1" in p.snippet


def test_soft_bounces_do_not_count_as_hard():
    for name, code in (("bounce_soft_422", "4.2.2"), ("bounce_delayed", "4.4.1")):
        p = parse_email(eml(name), ME)
        assert p.kind == "bounce" and p.status_code == code and p.soft and not p.hard_bounce


def test_plain_postfix_bounce_and_no_status():
    p = parse_email(eml("bounce_postfix_plain"), ME)
    assert p.hard_bounce and p.failed_recipients == ("hr@pm.test",) and p.status_code == "5.1.1"
    q = parse_email(eml("bounce_exchange_no_status"), ME)
    assert q.status_code is None and q.soft and not q.hard_bounce  # unknown code: never suppress on a guess


def test_failed_action_without_code_is_permanent():
    raw = eml("bounce_hard_511").replace(b"Status: 5.1.1\n", b"").replace(b"550-5.1.1", b"550")
    p = parse_email(raw, ME)
    assert p.status_code == "5.0.0" and p.hard_bounce


# ------------------------------------------------------------------ pure helpers
def test_normalizers():
    assert normalize_message_id(" <AbC@Mail.Test> ") == "abc@mail.test"
    assert normalize_message_id("plain@id") == "plain@id" and normalize_message_id(None) is None and normalize_message_id("<>") is None
    assert parse_id_list("<a@x> <B@x>\n <a@x>") == ("a@x", "b@x") and parse_id_list("a@x b@x") == ("a@x", "b@x")
    assert parse_id_list(None) == ()
    assert normalize_subject("RE: Re[2]: FWD: AW:  Hello   World ") == "hello world"
    assert normalize_subject(None) == ""


def test_extract_reply_text_markers():
    assert extract_reply_text("Yes.\n\n-----Original Message-----\nFrom: a@b.c\nold") == "Yes."
    assert extract_reply_text("Yes.\n> quoted\nmore") == "Yes.\nmore"
    assert extract_reply_text("OK\n\nFrom: Bob <bob@x.test>\nSent: Monday") == "OK"
    assert extract_reply_text("OK\n________________\nold") == "OK"
    assert extract_reply_text("Hi\r\nthere\r\n") == "Hi\nthere"


@pytest.mark.parametrize("text_,expected", [
    ("stop", True), ("STOP", True), ("Please stop emailing me", True), ("unsubscribe", True), ("Un-subscribe me", True),
    ("remove me from the list", True), ("Remove my email address", True), ("take me off your list", True),
    ("opt out", True), ("don't email me again", True), ("Hi\nthanks\nok\nStop.", True),
    ("Can you stop by on Friday?", False), ("we never stop hiring", False), ("it is a non-stop effort", False),
    ("I can't stop thinking about it", False), ("stopped by yesterday", False), ("Sounds good, let's talk.", False),
    ("", False), ("Hi\nthanks\nok\nsure\nstop", False),  # fifth line: not "the first lines"
])
def test_opt_out_phrases(text_, expected):
    assert detect_opt_out(text_) is expected


_FILLER = st.text(alphabet="abcdefghij klm.,!\n", max_size=60)  # no letter pair can spell a phrase


@settings(max_examples=80, deadline=None)
@given(prefix=_FILLER, suffix=_FILLER, phrase=st.sampled_from(["stop", "STOP", "Unsubscribe", "remove me", "Remove Me", "unsubscribe me please"]))
def test_opt_out_phrase_in_first_line_is_found(prefix, suffix, phrase):
    first = (prefix.replace("\n", " ").strip() + " " + phrase + " ").lstrip()
    assert detect_opt_out(first + "\n" + suffix)


@settings(max_examples=80, deadline=None)
@given(body=_FILLER)
def test_no_phrase_no_opt_out(body):
    assert not detect_opt_out(body)


@settings(max_examples=60, deadline=None)
@given(filler=st.lists(st.text(alphabet="abcdefg hij", min_size=1, max_size=20), min_size=4, max_size=8))
def test_phrase_after_first_lines_is_ignored(filler):
    lines = [f.strip() or "x" for f in filler]
    assert not detect_opt_out("\n".join(lines[:4] + ["stop", "unsubscribe"]))


@settings(max_examples=60, deadline=None)
@given(inner=_FILLER, domain=st.sampled_from(["a.test", "b.test"]))
def test_message_id_normalisation_roundtrip(inner, domain):
    mid = "id" + "".join(ch for ch in inner if ch.isalnum()) + "@" + domain
    for wrapped in (mid, f"<{mid}>", f"  <{mid.upper()}> "):
        assert normalize_message_id(wrapped) == mid.lower()
    assert parse_id_list(f"<x@y>\n <{mid.upper()}>")[-1] == mid.lower()


# ------------------------------------------------------------------ matching against the database
def test_match_by_headers_and_fallback(engine):
    configure(engine)
    lead, msg = sent_to(engine, "acme", "priya")
    beta, bmsg = sent_to(engine, "beta", "ravi")
    with engine.connect() as c:
        m = tracker.match_reply(c, parse_email(eml("reply_threaded"), ME))
        assert (m.lead_id, m.message_ref, m.how) == (lead, msg, "header")
        m = tracker.match_reply(c, parse_email(eml("reply_no_headers"), ME))  # sender in upper case + "RE:" subject
        assert (m.lead_id, m.message_ref, m.how) == (beta, bmsg, "sender_subject")
        assert tracker.match_reply(c, parse_email(eml("reply_wrong_subject"), ME)) is None  # same sender, other subject
        assert tracker.match_reply(c, parse_email(eml("newsletter_unrelated"), ME)) is None
        assert tracker.find_sent_message(c, ()) is None and tracker.find_by_sender_and_subject(c, "", "x") is None


def test_match_ignores_brackets_and_case_in_stored_ids(engine):
    configure(engine)
    lead, msg = sent_to(engine, "acme", "priya", message_id="SENT-Acme-1@Reachout.test")
    with engine.connect() as c:
        assert tracker.match_reply(c, parse_email(eml("reply_threaded"), ME)).message_ref == msg


def test_bounce_matching(engine):
    configure(engine)
    gone, msg = sent_to(engine, "gone", "nobody")
    dead, dmsg = sent_to(engine, "norig", "dead", message_id="<other@x>")
    with engine.connect() as c:
        m = tracker.match_bounce(c, parse_email(eml("bounce_hard_511"), ME))
        assert (m.lead_id, m.message_ref, m.how) == (gone, msg, "header")
        m = tracker.match_bounce(c, parse_email(eml("bounce_hard_no_original"), ME))  # by recipient
        assert (m.lead_id, m.message_ref, m.how) == (dead, dmsg, "recipient")
        assert tracker.match_bounce(c, parse_email(eml("bounce_forged_unknown_recipient"), ME)) is None


# ------------------------------------------------------------------ Gmail client (a fake imaplib connection)
class FakeConn:
    def __init__(self, mail: dict[int, bytes], *, login_error=False, validity=b"77", fetch_fail_uid=None, select_ok=True):
        self.mail, self.login_error, self.validity, self.fetch_fail_uid, self.select_ok = mail, login_error, validity, fetch_fail_uid, select_ok
        self.commands: list[tuple] = []
        self.logged_out = False

    def login(self, user, password):
        self.commands.append(("login", user))
        if self.login_error:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials " + password)

    def select(self, folder, readonly=False):
        self.commands.append(("select", folder, readonly))
        return ("OK" if self.select_ok else "NO", [b"1"])

    def response(self, name):
        return (name, [self.validity])

    def uid(self, cmd, *args):
        self.commands.append((cmd, *args))
        if cmd == "SEARCH":
            if args[0] == "UID":
                lo = int(args[1].split(":")[0])
                uids = [u for u in sorted(self.mail) if u >= lo] or [max(self.mail)]  # IMAP returns the last one for n:*
            else:
                uids = sorted(self.mail)
            return ("OK", [b" ".join(str(u).encode() for u in uids)])
        if cmd == "FETCH":
            uid = int(args[0])
            if uid == self.fetch_fail_uid:
                return ("NO", [])
            return ("OK", [(f"{uid} (UID {uid} BODY[]<0> {{9}}".encode(), self.mail[uid]), b")"])
        raise AssertionError(cmd)

    def logout(self):
        self.logged_out = True


def client(conn, password="s3cretpass", address=ME, **kw):
    made = []

    def factory(host, port, ssl_context=None, timeout=None):
        made.append((host, port, timeout))
        if isinstance(conn, Exception):
            raise conn
        return conn

    g = GmailImap(address, lambda: password, factory=factory, **kw)
    g.made = made
    return g


def test_gmail_imap_is_read_only_and_fetches_in_order():
    conn = FakeConn({1: b"a", 2: b"b", 5: b"c"})
    g = client(conn)
    res = g.fetch_new("INBOX", 1)
    assert res.uidvalidity == 77 and [(m.uid, m.raw) for m in res.messages] == [(2, b"b"), (5, b"c")]
    assert ("select", '"INBOX"', True) in conn.commands  # readonly
    verbs = {c[0] for c in conn.commands}
    assert verbs <= {"login", "select", "SEARCH", "FETCH"}  # nothing that can change the mailbox
    assert all("PEEK" in c[2] for c in conn.commands if c[0] == "FETCH")
    assert g.made[0][:2] == ("imap.gmail.com", 993)
    g.close()
    assert conn.logged_out
    g.close()  # idempotent


def test_gmail_imap_filters_the_star_quirk_and_first_run_uses_since():
    conn = FakeConn({3: b"x"})
    g = client(conn)
    assert g.fetch_new("INBOX", 3).messages == []  # "4:*" returned uid 3 anyway
    conn2 = FakeConn({1: b"a"})
    client(conn2).fetch_new("[Gmail]/All Mail", 0)
    assert any(c[0] == "SEARCH" and c[1] == "SINCE" for c in conn2.commands)
    assert ("select", '"[Gmail]/All Mail"', True) in conn2.commands


def test_gmail_imap_stops_at_first_failed_fetch():
    conn = FakeConn({1: b"a", 2: b"b", 3: b"c"}, fetch_fail_uid=2)
    assert [m.uid for m in client(conn).fetch_new("INBOX", 0).messages] == [1]


def test_gmail_imap_login_failure_does_not_leak_the_password():
    conn = FakeConn({}, login_error=True)
    g = client(conn, password="hunter2hunter2")
    with pytest.raises(ImapAuthError) as e:
        g.fetch_new("INBOX", 0)
    assert "hunter2" not in str(e.value) and conn.logged_out


def test_gmail_imap_without_credentials_never_connects():
    g = client(FakeConn({}), password=None)
    with pytest.raises(ImapAuthError):
        g.fetch_new("INBOX", 0)
    assert g.made == []
    with pytest.raises(ImapAuthError):
        client(FakeConn({}), address="").fetch_new("INBOX", 0)


def test_gmail_imap_network_errors_are_transient():
    with pytest.raises(ImapTransientError):
        client(OSError("down")).fetch_new("INBOX", 0)
    conn = FakeConn({}, select_ok=False)
    with pytest.raises(ImapTransientError):
        client(conn).fetch_new("INBOX", 0)

    class Boom(FakeConn):
        def login(self, *a):
            raise OSError("reset")

    with pytest.raises(ImapTransientError):
        client(Boom({})).fetch_new("INBOX", 0)

    class SearchFails(FakeConn):
        def uid(self, cmd, *a):
            return ("NO", []) if cmd == "SEARCH" else super().uid(cmd, *a)

    with pytest.raises(ImapTransientError):
        client(SearchFails({1: b"a"})).fetch_new("INBOX", 0)


def test_gmail_imap_odd_responses():
    assert GmailImap._uidvalidity(type("C", (), {"response": lambda s, n: (n, [None])})()) == 0
    conn = FakeConn({1: b"a"})
    g = client(conn)
    conn.uid = lambda cmd, *a: ("OK", [b")"]) if cmd == "FETCH" else ("OK", [None] if False else [b"1"])
    assert g.fetch_new("INBOX", 0).messages == []  # a FETCH with no literal is treated as a failure, nothing skipped


def test_from_settings_reads_keychain(engine, memory_keyring):
    configure(engine)
    from backend.core import secrets

    secrets.set_app_password(ME, "abcd efgh ijkl mnop")
    g = GmailImap.from_settings(engine)
    assert g.address == ME and g._password_provider() == "abcdefghijklmnop"
    assert db.load_settings(engine).gmail_address == ME


def test_priority_table_orders_strength():
    p = tracker.LEAD_PRIORITY
    assert p["opted_out"] > p["bounced"] > p["closed"] > p["replied"] > p["auto_reply"] > p["sent"]
