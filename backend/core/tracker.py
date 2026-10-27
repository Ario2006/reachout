"""Reading Gmail and understanding what came back (milestone 5).

Two halves:

* `GmailImap` implements `ports.ImapClient`. It is READ-ONLY by construction: the mailbox is opened with
  `select(readonly=True)` and the only commands it ever issues are LOGIN, SELECT, UID SEARCH, UID FETCH
  (`BODY.PEEK`) and LOGOUT. It never stores flags, deletes, expunges, copies or moves anything. The App Password is
  read from the Keychain at connect time, handed to `login()` and never kept, logged or put in an error message.
* Pure functions over raw RFC 822 bytes (stdlib `email` only): `parse_email` returns a `Parsed` with a `kind` of
  `reply`, `opt_out`, `auto_reply`, `bounce` or `ignore`. Matching a parsed email to one of our sent messages
  (`find_sent_message`, `find_lead`) takes a SQLAlchemy connection.

Classification order (first hit wins): from ourselves -> ignore; delivery report (DSN) -> bounce; auto-reply
headers or subject -> auto_reply; first lines say stop/unsubscribe/remove me -> opt_out; otherwise reply.
An auto-reply is never a reply and never an opt-out, even if it contains the word "unsubscribe".
The opt-out detector deliberately errs towards suppressing; a false opt-out can only be undone by removing the
address from the Settings suppression list (and only entries added by hand are removable there).
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timezone
from email import policy
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Literal

from sqlalchemy import Connection, Engine, text

from backend.core import config, db, secrets
from backend.core.ports import FetchResult, ImapAuthError, IncomingMessage

log = logging.getLogger("reachout.tracker")

Kind = Literal["reply", "opt_out", "auto_reply", "bounce", "ignore"]

# Lead status strength. A weaker outcome never overwrites a stronger one: a bounced or opted-out lead can not become
# "replied" again, and a replied lead can not fall back to "auto_reply". `closed` is the user's own decision, so a
# hard bounce or an opt-out (facts about the address) still outranks it.
LEAD_PRIORITY: dict[str, int] = {
    "new": 0, "drafted": 1, "approved": 2, "queued": 3, "sent": 4,
    "auto_reply": 5, "replied": 6, "closed": 7, "bounced": 8, "opted_out": 9,
}
EVENT_STATUS: dict[str, str] = {"reply": "replied", "auto_reply": "auto_reply", "opt_out": "opted_out"}

FOLDERS: tuple[str, ...] = ("INBOX",)  # see inbox_poller for why All Mail is not polled


# ------------------------------------------------------------------------------------------------ IMAP client
class ImapTransientError(Exception):
    """Network or protocol trouble talking to Gmail. Not a login problem; the next poll retries."""


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


# (folder, uidvalidity, uid) -> consecutive polls on which downloading that message failed. Lives in the worker
# process (the client object itself is recreated for every poll).
_FETCH_FAILURES: dict[tuple[str, int, int], int] = {}
MAX_FETCH_FAILURES = 3


class GmailImap:
    """Read-only IMAP over SSL to imap.gmail.com:993.

    After each `fetch_new`, `last_problem` is None or a sentence about a message that could not be downloaded. Such a
    message stops the batch (so the cursor does not pass it) for 2 polls and is skipped on the 3rd."""

    last_problem: str | None = None

    PARTIAL_BYTES = 262_144  # a reply or a DSN is far smaller; huge attachments are never downloaded
    MAX_BATCH = 500

    def __init__(self, address: str, password_provider: Callable[[], str | None], *, host: str = config.IMAP_HOST,
                 port: int = config.IMAP_PORT, since_days: int = 30, timeout: float = 30,
                 factory: Callable[..., imaplib.IMAP4] | None = None) -> None:
        self.address = address
        self._password_provider = password_provider
        self.host, self.port, self.since_days, self.timeout = host, port, since_days, timeout
        self._factory = factory or imaplib.IMAP4_SSL
        self._conn: imaplib.IMAP4 | None = None

    @classmethod
    def from_settings(cls, engine: Engine | None = None) -> GmailImap:
        address = db.load_settings(engine).gmail_address
        return cls(address, lambda: secrets.get_app_password(address))

    # -- connection
    def _connect(self) -> imaplib.IMAP4:
        if self._conn is not None:
            return self._conn
        password = self._password_provider() if self.address else None
        if not self.address or not password:
            raise ImapAuthError("No Gmail address or App Password is saved. Add them in Settings.")
        try:
            conn = self._factory(self.host, self.port, ssl_context=ssl.create_default_context(), timeout=self.timeout)
        except (OSError, imaplib.IMAP4.error) as e:
            raise ImapTransientError(f"Could not connect to {self.host}: {e}") from None
        try:
            conn.login(self.address, password)
        except imaplib.IMAP4.error:
            self._logout(conn)
            raise ImapAuthError("Gmail rejected the login. Check the App Password and that IMAP is enabled.") from None
        except OSError as e:
            self._logout(conn)
            raise ImapTransientError(f"Connection problem while logging in: {e}") from None
        self._conn = conn
        return conn

    @staticmethod
    def _logout(conn: imaplib.IMAP4) -> None:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            self._logout(conn)

    # -- reading
    def fetch_new(self, folder: str, after_uid: int) -> FetchResult:
        conn = self._connect()
        try:
            typ, _ = conn.select(_quote(folder), readonly=True)
            if typ != "OK":
                raise ImapTransientError(f"Could not open folder {folder!r}")
            uidvalidity = self._uidvalidity(conn)
            criteria = ["UID", f"{after_uid + 1}:*"]
            if after_uid == 0 and self.since_days:  # first look at a mailbox: do not download years of mail
                from datetime import date, timedelta
                since = date.today() - timedelta(days=self.since_days)
                criteria = ["SINCE", since.strftime("%d-%b-%Y")]
            typ, data = conn.uid("SEARCH", *criteria)
            if typ != "OK":
                raise ImapTransientError("Gmail search failed")
            # "n:*" always returns the newest message even when it is not newer than n: filter again.
            uids = sorted(u for u in (int(x) for x in (data[0] or b"").split()) if u > after_uid)[: self.MAX_BATCH]
            messages: list[IncomingMessage] = []
            self.last_problem = None
            for uid in uids:
                key = (folder, uidvalidity, uid)
                raw = self._fetch_one(conn, uid)
                if raw is None:
                    n = _FETCH_FAILURES.get(key, 0) + 1
                    if n >= MAX_FETCH_FAILURES:  # a message that never downloads must not block the mailbox forever
                        _FETCH_FAILURES.pop(key, None)
                        self.last_problem = f"Skipped email {uid} in {folder}: it could not be downloaded {n} times in a row."
                        continue
                    _FETCH_FAILURES[key] = n
                    self.last_problem = f"Could not download email {uid} in {folder} (attempt {n} of {MAX_FETCH_FAILURES}); will retry."
                    break  # stop here so the next poll retries from this message and nothing is passed over
                _FETCH_FAILURES.pop(key, None)
                messages.append(IncomingMessage(uid, raw))
            return FetchResult(uidvalidity, messages)
        except (imaplib.IMAP4.error, OSError) as e:
            self.close()
            raise ImapTransientError(f"Gmail connection problem: {e}") from None

    @staticmethod
    def _uidvalidity(conn: imaplib.IMAP4) -> int:
        _, vals = conn.response("UIDVALIDITY")
        try:
            return int(vals[0])
        except (TypeError, ValueError, IndexError):
            return 0

    def _fetch_one(self, conn: imaplib.IMAP4, uid: int) -> bytes | None:
        typ, data = conn.uid("FETCH", str(uid), f"(BODY.PEEK[]<0.{self.PARTIAL_BYTES}>)")
        if typ != "OK":
            return None
        for part in data or []:
            if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                return part[1]
        return None


# ------------------------------------------------------------------------------------------------ parsing
@dataclass(frozen=True)
class Parsed:
    kind: Kind
    message_id: str | None  # the incoming email's own Message-ID, normalised
    from_addr: str
    from_name: str
    subject: str
    occurred_at: str | None  # ISO-8601 UTC from the Date header; None when missing or unreadable
    text: str  # the new text of the email: quoted history removed
    in_reply_to: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    # bounces
    status_code: str | None = None
    soft: bool = False
    failed_recipients: tuple[str, ...] = ()
    original_ids: tuple[str, ...] = ()  # Message-IDs found inside the returned original message
    reason: str = field(default="", compare=False)

    @property
    def hard_bounce(self) -> bool:
        return self.kind == "bounce" and not self.soft and bool(self.status_code) and self.status_code.startswith("5")

    @property
    def snippet(self) -> str:
        body = re.sub(r"\s+", " ", self.text).strip()
        if self.kind == "bounce":
            body = self.reason or body
        return body[:300]

    @property
    def reply_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.in_reply_to, *self.references, *self.original_ids)))


_ID_RE = re.compile(r"<([^<>\s]+)>")
_ADDR_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS_RE = re.compile(r"(?<![\d.])([245])\.(\d{1,3})\.(\d{1,3})(?![\d.])")


def normalize_message_id(value: str | None) -> str | None:
    if not value:
        return None
    m = _ID_RE.search(value)
    v = (m.group(1) if m else value).strip().strip("<>").strip().lower()
    return v or None


def parse_id_list(value: str | None) -> tuple[str, ...]:
    """In-Reply-To / References: every <id> in order, without brackets, lowercase."""
    if not value:
        return ()
    found = _ID_RE.findall(value) or value.split()
    out = [n for n in (normalize_message_id(x) for x in found) if n]
    return tuple(dict.fromkeys(out))


_SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd|aw|sv|antw|wg|tr|rv)\s*(?:\[\d+\])?\s*:\s*)+", re.I)


def normalize_subject(subject: str | None) -> str:
    s = _SUBJECT_PREFIX.sub("", subject or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def _header(msg: Message, name: str) -> str:
    try:
        v = msg.get(name)
        return "" if v is None else re.sub(r"\s+", " ", str(v)).strip()
    except Exception:  # noqa: BLE001 - malformed headers must never break the poll
        return ""


def _decoded_payload(part: Message) -> str:
    try:
        content = part.get_content()
        if isinstance(content, str):
            return content
    except Exception:  # noqa: BLE001 - unknown charset, bad transfer encoding: fall back to lenient decoding
        pass
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001
        payload = None
    if isinstance(payload, bytes):
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace") \
            if _known_charset(part.get_content_charset()) else payload.decode("utf-8", errors="replace")
    return ""


def _known_charset(name: str | None) -> bool:
    if not name:
        return False
    try:
        "".encode(name)
        return True
    except LookupError:
        return False


_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)


def _html_to_text(html: str) -> str:
    html = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", html, flags=re.I)
    t = _TAG_RE.sub("", html)
    return re.sub(r"&nbsp;", " ", re.sub(r"&amp;", "&", t))


def body_text(msg: Message) -> str:
    """Best-effort readable body: text/plain preferred, HTML stripped otherwise. Never raises."""
    try:
        for want in ("plain", "html"):
            for part in msg.walk():
                if part.is_multipart() or part.get_content_maintype() != "text" or part.get_content_subtype() != want:
                    continue
                if part.get_content_disposition() == "attachment":
                    continue
                t = _decoded_payload(part)
                if t.strip():
                    return _html_to_text(t) if want == "html" else t
    except Exception:  # noqa: BLE001
        log.debug("could not read body", exc_info=True)
    return ""


_QUOTE_STOP = [
    re.compile(r"^On .{5,300}wrote:\s*$", re.I),
    re.compile(r"^-{2,}\s*(original message|forwarded message)\b.*$", re.I),
    re.compile(r"^_{8,}\s*$"),
    re.compile(r"^(From|De|Von):\s.+<?[^<>\s]+@[^<>\s]+>?\s*$", re.I),
]


def extract_reply_text(text_: str) -> str:
    """The new text of a reply: stops at the first quoted-history marker and drops '>' lines."""
    lines = text_.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if any(p.match(s) for p in _QUOTE_STOP):
            break
        if s.lower().startswith("on ") and not s.lower().endswith("wrote:") and i + 1 < len(lines) \
                and re.match(r"^.{0,200}wrote:\s*$", lines[i + 1].strip(), re.I):
            break  # Gmail wraps "On <date>, <name> <addr>\nwrote:" onto two lines
        if s.startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


# -- opt-out
_OPT_PHRASES = re.compile(
    r"(?<![\w'’-])(?:un-?subscribe\w*|remove\s+me|remove\s+(?:my|this)\s+(?:e-?mail|address|name|id)|take\s+me\s+off"
    r"|opt[\s-]?out|(?:do\s*n[o']?t|do\s+not|never)\s+(?:e-?mail|contact|write\s+to|message)\s+me"
    r"|stop(?!\s+(?:by|in|over|thinking|hesitating|me\s+from|worrying))\b)",
    re.I)
_NEGATED_STOP = re.compile(r"\b(?:don'?t|do not|can'?t|cannot|won'?t|never|non|bus|full)[\s-]+stop", re.I)
OPT_OUT_LINES = 4


def detect_opt_out(new_text: str) -> bool:
    """True when one of the first few non-empty lines of the NEW text asks us to stop (stop, unsubscribe, remove me
    and close synonyms). Quoted history must already be removed: our own footer says 'reply stop'."""
    lines = [ln.strip() for ln in new_text.splitlines() if ln.strip()][:OPT_OUT_LINES]
    for ln in lines:
        scrubbed = _NEGATED_STOP.sub(" ", ln)
        if _OPT_PHRASES.search(scrubbed):
            return True
    return False


# -- auto replies
_AUTO_SUBJECT = re.compile(
    r"out[\s-]+of[\s-]+(?:the[\s-]+)?office|automatic\s+reply|auto[\s-]?reply|auto[\s-]?response|autoreply"
    r"|away\s+from\s+(?:my\s+)?(?:the\s+)?(?:office|desk|email)|on\s+(?:vacation|holiday|leave)\b|vacation\s+reply"
    r"|abwesenheit|automatische\s+antwort|r[eé]ponse\s+automatique|\bOOO\b", re.I)


def is_auto_reply(msg: Message, subject: str) -> bool:
    auto = _header(msg, "Auto-Submitted").lower()
    if auto and auto != "no":
        return True
    if _header(msg, "X-Autoreply") or _header(msg, "X-Autorespond"):
        return True
    if _header(msg, "Precedence").lower() in {"bulk", "auto_reply", "junk"}:
        return True
    return bool(_AUTO_SUBJECT.search(subject))


# -- bounces
_DAEMON_LOCALS = {"mailer-daemon", "postmaster", "mail-daemon"}
_DAEMON_NAMES = re.compile(r"mail delivery (?:sub)?system|mailer[-\s]daemon|postmaster", re.I)


def _addr_of(value: str | None) -> str:
    if not value:
        return ""
    m = _ADDR_RE.search(value)
    return m.group(0).lower() if m else ""


def _walk_dsn(msg: Message) -> tuple[list[Message], list[str]]:
    """Per-recipient blocks of any message/delivery-status part, and the Message-IDs of the returned original."""
    blocks: list[Message] = []
    original_ids: list[str] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            payload = part.get_payload()
            if isinstance(payload, list):
                blocks.extend(b for b in payload if isinstance(b, Message))
            elif isinstance(payload, str):
                blocks.extend(email.message_from_string(chunk) for chunk in re.split(r"\n\s*\n", payload) if chunk.strip())
        elif ctype in {"message/rfc822", "text/rfc822-headers"}:
            payload = part.get_payload()
            inner: list[Message] = []
            if isinstance(payload, list):
                inner = [p for p in payload if isinstance(p, Message)]
            elif isinstance(payload, str):
                inner = [email.message_from_string(payload)]
            for m in inner:
                mid = normalize_message_id(_header(m, "Message-ID"))
                if mid:
                    original_ids.append(mid)
    return blocks, list(dict.fromkeys(original_ids))


def _parse_bounce(msg: Message, text_: str) -> tuple[str | None, bool, tuple[str, ...], tuple[str, ...], str]:
    blocks, original_ids = _walk_dsn(msg)
    recipients: list[str] = []
    codes: list[str] = []
    delayed = False
    failed = False
    reason = ""
    for b in blocks:
        r = _addr_of(_header(b, "Final-Recipient")) or _addr_of(_header(b, "Original-Recipient"))
        if r:
            recipients.append(r)
        status = _header(b, "Status")
        m = _STATUS_RE.search(status)
        if m:
            codes.append(m.group(0))
        action = _header(b, "Action").lower()
        delayed = delayed or action == "delayed"
        failed = failed or action == "failed"
        reason = reason or _header(b, "Diagnostic-Code")
    if not recipients:  # not a structured DSN: take addresses from the text, minus the daemon's own
        recipients = [a.lower() for a in _ADDR_RE.findall(text_)]
        recipients = [a for a in recipients if a.split("@")[0] not in _DAEMON_LOCALS]
    if not codes:
        m = _STATUS_RE.search(text_)
        if m:
            codes.append(m.group(0))
    code = next((c for c in codes if c.startswith("5")), None) or (codes[0] if codes else None)
    if code is None and failed:
        code = "5.0.0"  # RFC 3464: Action failed means permanent failure
    soft = delayed or (code is not None and code.startswith("4")) or code is None
    if not reason:
        lines = [ln.strip() for ln in text_.splitlines() if ln.strip()]
        line = next((ln for ln in lines if code and code in ln), None) or next(iter(lines), "")
        reason = re.sub(r"\s+", " ", line)
    if code and not soft:
        reason = f"Delivery failed ({code}): {reason}"[:300]
    elif code:
        reason = f"Temporary delivery problem ({code}): {reason}"[:300]
    return code, soft, tuple(dict.fromkeys(recipients)), tuple(original_ids), reason


def _is_dsn(msg: Message, from_addr: str, from_name: str) -> bool:
    if msg.get_content_type() == "multipart/report":
        rt = str(msg.get_param("report-type") or "").lower()
        if rt in {"", "delivery-status"}:
            return True
        return False
    local = from_addr.split("@")[0]
    return local in _DAEMON_LOCALS or bool(_DAEMON_NAMES.search(from_name))


def _is_mdn(msg: Message) -> bool:
    return msg.get_content_type() == "multipart/report" and "disposition-notification" in str(msg.get_param("report-type") or "").lower()


def _date_iso(msg: Message) -> str | None:
    raw = _header(msg, "Date")
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if d is None:
        return None
    d = d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_email(raw: bytes, own_address: str = "") -> Parsed:
    """Classify one raw message. Never raises: unreadable mail comes back as kind 'ignore'."""
    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception:  # noqa: BLE001 - the parser is lenient but not infallible
        log.debug("unparseable message", exc_info=True)
        return Parsed("ignore", None, "", "", "", None, "")
    name, addr = parseaddr(_header(msg, "From"))
    from_addr = (addr or _addr_of(_header(msg, "From"))).lower()
    subject = _header(msg, "Subject")
    common = dict(
        message_id=normalize_message_id(_header(msg, "Message-ID")), from_addr=from_addr, from_name=name,
        subject=subject, occurred_at=_date_iso(msg),
        in_reply_to=parse_id_list(_header(msg, "In-Reply-To")), references=parse_id_list(_header(msg, "References")))
    full_text = body_text(msg)
    if own_address and from_addr == own_address.strip().lower():
        return Parsed("ignore", text=extract_reply_text(full_text), **common)
    if _is_mdn(msg):
        return Parsed("ignore", text="", **common)
    if _is_dsn(msg, from_addr, name):
        code, soft, recipients, original_ids, reason = _parse_bounce(msg, full_text)
        return Parsed("bounce", text=full_text, status_code=code, soft=soft, failed_recipients=recipients,
                      original_ids=original_ids, reason=reason, **common)
    new_text = extract_reply_text(full_text)
    if is_auto_reply(msg, subject):
        return Parsed("auto_reply", text=new_text, **common)
    kind: Kind = "opt_out" if detect_opt_out(new_text) else "reply"
    return Parsed(kind, text=new_text, **common)


# ------------------------------------------------------------------------------------------------ matching
@dataclass(frozen=True)
class Match:
    lead_id: int
    message_ref: int | None
    lead_email: str
    how: Literal["header", "sender_subject", "recipient"]


def _norm_sql(col: str) -> str:
    return f"lower(trim({col}, '<> '))"


def find_sent_message(c: Connection, ids: tuple[str, ...]) -> Match | None:
    """Our sent message whose Message-ID appears in `ids` (In-Reply-To, References or a bounce's returned original)."""
    if not ids:
        return None
    marks = ", ".join(f":i{n}" for n in range(len(ids)))
    row = c.execute(text(
        f"SELECT m.id, m.lead_id, l.email FROM messages m JOIN leads l ON l.id = m.lead_id "
        f"WHERE m.message_id IS NOT NULL AND {_norm_sql('m.message_id')} IN ({marks}) ORDER BY m.id DESC LIMIT 1"),
        {f"i{n}": v for n, v in enumerate(ids)}).first()
    return Match(row[1], row[0], row[2], "header") if row else None


def find_by_sender_and_subject(c: Connection, from_addr: str, subject: str) -> Match | None:
    """Fallback for clients that drop the threading headers: the sender is a lead we wrote to, same subject."""
    if not from_addr:
        return None
    lead = c.execute(text("SELECT id, email FROM leads WHERE email = :e"), {"e": from_addr.lower()}).first()
    if not lead:
        return None
    wanted = normalize_subject(subject)
    for mid, subj in c.execute(text(
            "SELECT id, subject FROM messages WHERE lead_id = :l AND sent_at IS NOT NULL ORDER BY id DESC"), {"l": lead[0]}):
        if wanted and normalize_subject(subj) == wanted:
            return Match(lead[0], mid, lead[1], "sender_subject")
    return None


def match_reply(c: Connection, p: Parsed) -> Match | None:
    return find_sent_message(c, p.reply_ids) or find_by_sender_and_subject(c, p.from_addr, p.subject)


def match_bounce(c: Connection, p: Parsed) -> Match | None:
    """By the returned original's Message-ID (or the bounce's own threading headers); else by the failed recipient,
    which must be a lead we actually emailed, so a forged report can not suppress an arbitrary address."""
    hit = find_sent_message(c, p.reply_ids)
    if hit:
        return hit
    for rcpt in p.failed_recipients:
        row = c.execute(text(
            "SELECT l.id, l.email, (SELECT m.id FROM messages m WHERE m.lead_id = l.id AND m.sent_at IS NOT NULL "
            "ORDER BY m.id DESC LIMIT 1) FROM leads l WHERE l.email = :e "
            "AND EXISTS (SELECT 1 FROM messages m WHERE m.lead_id = l.id AND m.sent_at IS NOT NULL)"),
            {"e": rcpt.lower()}).first()
        if row:
            return Match(row[0], row[2], row[1], "recipient")
    return None
