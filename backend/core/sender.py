"""Sender worker (milestone 4): the only code that hands an email to the Mailer.

Every few seconds `Sender.tick` visits each running campaign and, if its jittered `next_send_at` has passed, does ONE
send:  bounce guard -> outbox.next_eligible -> outbox.claim (all send-safety rules, atomically) -> footer -> store the
Message-ID -> Mailer.send -> mark_sent / mark_failed.  The outcome rules:

    sent                         -> sent, lead 'sent'
    RecipientRejected            -> failed (nothing went out; the user may Retry)
    TransientSendError           -> back to queued (released), retried after a backoff; failed after 3 tries
    OutcomeUnknownError / other  -> needs_confirmation, NEVER resent automatically
    SmtpAuthError                -> released to queued, every running campaign paused (paused_by smtp_login), no retry loop

A crash between claim and mark_sent leaves the message in `sending`; `outbox.recover_stale` turns it into
needs_confirmation when the worker restarts.
"""
from __future__ import annotations

import logging
import os
import random
import shutil
import subprocess
import threading
from collections.abc import Callable

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy import Engine, text

from backend.core import campaigns, db, event_types, footer, mailer as mailer_mod, outbox, services, slog
from backend.core.jobs import JobContext
from backend.core.models import AppSettings
from backend.core.ports import MailerError, OutgoingEmail, RecipientRejected, SmtpAuthError, TransientSendError

log = logging.getLogger("reachout.sender")
TICK_EVERY_S = 3
MAX_TRANSIENT_TRIES = 3
TRANSIENT_BACKOFF_S = (60.0, 120.0, 300.0)
TIME_REASONS = ("window", "daily_cap", "domain_daily_limit", "domain_gap", "not_before", "paused_all")  # "later", not "never"


class Sender:
    def __init__(self, engine: Engine, rng: random.Random | None = None, idle_backoff_s: float = 20.0,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen, which: Callable[[str], str | None] = shutil.which) -> None:
        self.engine = engine
        self.rng = rng or random.Random()
        self.idle_backoff_s = idle_backoff_s
        self._popen, self._which = popen, which
        self._lock = threading.Lock()  # one tick at a time
        self.stopping = threading.Event()
        self._idle_until: dict[int, float] = {}
        self._transient: dict[int, int] = {}
        self._caffeinate: subprocess.Popen | None = None

    # ------------------------------------------------------------------ tick
    def tick(self) -> int:
        """One pass over the running campaigns. Returns how many emails were handed to the mailer."""
        if self.stopping.is_set() or not self._lock.acquire(blocking=False):
            return 0
        sent = 0
        try:
            with self.engine.connect() as c:
                running = [dict(r) for r in c.execute(text("SELECT * FROM campaigns WHERE state='running' ORDER BY id")).mappings()]
                paused = outbox.paused_all(c)
            self._sync_caffeinate(bool(running) and not paused)
            if paused or not running:
                return 0
            settings = db.load_settings(self.engine)
            for cp in running:
                if self.stopping.is_set():
                    break
                sent += self._step(cp, settings)
        except Exception:  # noqa: BLE001 - one bad pass must not kill the scheduler job
            log.exception("sender tick failed")
        finally:
            self._lock.release()
        return sent

    def _step(self, cp: dict, settings: AppSettings) -> int:
        svc = services.get_services()
        now = svc.clock.now()
        cid = cp["id"]
        if cp["next_send_at"] and now < cp["next_send_at"]:
            return 0
        if self._idle_until.get(cid, 0) > now:
            return 0
        if self._bounce_guard(cid, settings):
            return 0
        if now < self._spacing_floor(cp, settings):
            return 0
        outbox.resync_snapshots(cid, self.engine)
        ids = outbox.next_eligible(cid, now, settings, self.engine, limit=1)
        if not ids:
            if not self._finish_if_empty(cid):
                self._idle_until[cid] = now + self.idle_backoff_s
            return 0
        return self._send(cp, ids[0], settings, svc, now)

    # ------------------------------------------------------------------ guards
    def _spacing_floor(self, cp: dict, settings: AppSettings) -> float:
        """Earliest time of the next send judged from what was actually claimed, so a crash that lost `next_send_at`
        (or a restart) can never produce two emails closer together than the minimum delay."""
        lo = cp["delay_min_s"] if cp["delay_min_s"] is not None else settings.delay_min_s
        with self.engine.connect() as c:
            last = c.execute(text("SELECT MAX(claimed_at) FROM messages WHERE campaign_id=:c AND state IN ('sending','sent','needs_confirmation')"), {"c": cp["id"]}).scalar()
        return (last + lo) if last is not None else 0.0

    def _bounce_guard(self, cid: int, settings: AppSettings) -> bool:
        sent, bounced = campaigns.bounce_stats(cid, self.engine)
        if not sent or sent < settings.bounce_pause_min_sends:
            return False
        rate = bounced / sent * 100
        acked = int(campaigns.get_app_state(f"bounce_ack:{cid}", self.engine) or 0)
        if rate > settings.bounce_pause_pct and bounced > acked:
            reason = (f"Paused automatically: {bounced} of {sent} emails bounced ({rate:.0f}%), above the {settings.bounce_pause_pct:g}% limit. "
                      "Check the addresses in this campaign before resuming.")
            campaigns.pause(cid, "bounce_rate", reason, self.engine)
            slog.log_event("campaign.bounce_pause", campaign_id=cid, bounced=bounced, sent=sent)
            return True
        return False

    def _finish_if_empty(self, cid: int) -> bool:
        with self.engine.begin() as c:
            left = c.execute(text("SELECT COUNT(*) FROM messages WHERE campaign_id=:c AND state IN ('queued','sending')"), {"c": cid}).scalar_one()
            if left:
                return False
            res = c.execute(text("UPDATE campaigns SET state='done', finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), next_send_at=NULL WHERE id=:c AND state='running'"), {"c": cid})
        if res.rowcount:
            campaigns.publish_campaign(cid, self.engine)
            campaigns.publish_sending(self.engine)
        return bool(res.rowcount)

    # ------------------------------------------------------------------ one send
    def _send(self, cp: dict, mid: int, settings: AppSettings, svc: services.Services, now: float) -> int:
        cid = cp["id"]
        if not settings.gmail_address:
            self._stop_run("Gmail is not set up: add your address and App Password in Settings.")
            return 0
        try:
            mailer = svc.mailer()
        except SmtpAuthError as e:
            self._stop_run(str(e))
            return 0
        try:
            claim = outbox.claim(mid, now, settings, self.engine)
            if not claim.ok:
                if claim.retry_at:
                    self._idle_until[cid] = claim.retry_at
                return 0
            return self._deliver(cp, mid, claim.attempt_id, settings, mailer)
        finally:
            try:
                mailer.close()
            except Exception:  # noqa: BLE001
                pass

    def _deliver(self, cp: dict, mid: int, attempt: str, settings: AppSettings, mailer) -> int:
        msg = outbox.get_message(mid, self.engine) or {}
        message_id = mailer_mod.new_message_id(settings.gmail_address)
        outbox.set_message_id(mid, attempt, message_id, self.engine)
        email = OutgoingEmail(
            from_addr=settings.gmail_address, from_name=settings.display_name, to_addr=msg["recipient_email"], subject=msg["subject"] or "",
            body=footer.ensure_footer(msg["body"] or "", settings, cp["mode"]), message_id=message_id,
            in_reply_to=msg.get("in_reply_to") or None, references=tuple((msg.get("references_text") or "").split()),
            attachment_path=cp["attachment_path"] or None)
        self._publish_msg(msg, "sending")
        delay = self._delay(cp, settings)
        try:
            receipt = mailer.send(email)
        except SmtpAuthError as e:
            outbox.release(mid, attempt, "Not sent: Gmail login or account problem.", self.engine)
            self._publish_msg(msg, "queued")
            self._stop_run(str(e))
            return 1
        except TransientSendError as e:
            n = self._transient.get(mid, 0) + 1
            self._transient[mid] = n
            if n >= MAX_TRANSIENT_TRIES:
                outbox.mark_failed(mid, attempt, f"Gave up after {n} tries: {e}", False, self.engine)
            else:
                outbox.release(mid, attempt, str(e), self.engine)
                delay = TRANSIENT_BACKOFF_S[min(n - 1, len(TRANSIENT_BACKOFF_S) - 1)]
        except RecipientRejected as e:
            outbox.mark_failed(mid, attempt, str(e), False, self.engine)
        except MailerError as e:
            outbox.mark_failed(mid, attempt, str(e), e.outcome_unknown, self.engine)
        except Exception as e:  # noqa: BLE001 - anything unexpected: the message may have gone out
            log.exception("unexpected error while sending message %s", mid)
            outbox.mark_failed(mid, attempt, f"Unexpected error ({type(e).__name__}); it may or may not have been sent.", True, self.engine)
        else:
            if not outbox.mark_sent(mid, attempt, message_id, receipt.smtp_result, self.engine):
                log.error("message %s was sent but could not be marked sent", mid)
            if msg.get("kind") == "initial":
                with self.engine.begin() as c:
                    c.execute(text("UPDATE leads SET status='sent', last_activity_at=datetime('now') WHERE id=:i AND status IN ('new','drafted','approved','queued')"), {"i": msg["lead_id"]})
        final = outbox.get_message(mid, self.engine) or {}
        self._publish_msg(msg, final.get("state", "needs_confirmation"))
        self._schedule_next(cp["id"], delay)
        campaigns.publish_sending(self.engine)
        return 1

    # ------------------------------------------------------------------ helpers
    def _delay(self, cp: dict, settings: AppSettings) -> float:
        lo = cp["delay_min_s"] if cp["delay_min_s"] is not None else settings.delay_min_s
        hi = cp["delay_max_s"] if cp["delay_max_s"] is not None else settings.delay_max_s
        return self.rng.uniform(lo, max(lo, hi))

    def _schedule_next(self, cid: int, delay: float) -> None:
        at = services.get_services().clock.now() + delay
        with self.engine.begin() as c:
            c.execute(text("UPDATE campaigns SET next_send_at=:t WHERE id=:i"), {"t": at, "i": cid})
        campaigns.publish_campaign(cid, self.engine)

    def _stop_run(self, reason: str) -> None:
        """Login/account failure: every running campaign stops. Nothing retries; the user fixes Gmail and presses Resume."""
        n = campaigns.pause_all_running("smtp_login", reason, self.engine)
        slog.log_event("sender.auth_stop", campaigns=n)
        log.warning("sending stopped: %s", reason)

    def _publish_msg(self, msg: dict, state: str) -> None:
        if not msg:
            return
        event_types.publish("message", event_types.MessageEvent(campaign_id=msg["campaign_id"], lead_id=msg["lead_id"], message_id=msg["id"],
                                                                kind=msg["kind"], state=state), self.engine)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ caffeinate
    def _sync_caffeinate(self, wanted: bool) -> None:
        running = self._caffeinate is not None and self._caffeinate.poll() is None
        if wanted and not running:
            exe = self._which("caffeinate")
            if exe:
                try:
                    self._caffeinate = self._popen([exe, "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    running = True
                except OSError:
                    self._caffeinate = None
        elif not wanted and running:
            self._stop_caffeinate()
            running = False
        flag = "1" if running else "0"
        if campaigns.get_app_state("caffeinate_active", self.engine) != flag:
            campaigns.set_app_state("caffeinate_active", flag, self.engine)

    def _stop_caffeinate(self) -> None:
        p, self._caffeinate = self._caffeinate, None
        if p is not None and p.poll() is None:
            p.terminate()

    # ------------------------------------------------------------------ shutdown
    def shutdown(self, wait_s: float = 25.0) -> bool:
        """Stop picking up work, let an in-flight send finish (bounded), then pause running campaigns (paused_by
        shutdown). Returns False if a send was still in flight: it stays in `sending` and recover_stale handles it."""
        self.stopping.set()
        done = self._lock.acquire(timeout=wait_s)
        try:
            self._stop_caffeinate()
            campaigns.set_app_state("caffeinate_active", "0", self.engine)
            campaigns.pause_for_shutdown(self.engine)
        finally:
            if done:
                self._lock.release()
        return done


_instance: Sender | None = None


def register(sched: BaseScheduler, engine: Engine) -> None:
    """Adds the send-tick job to the worker's scheduler."""
    global _instance
    _instance = Sender(engine)
    sched.add_job(_instance.tick, "interval", seconds=TICK_EVERY_S, id="sender_tick", max_instances=1, coalesce=True)


def shutdown(wait_s: float = 25.0) -> bool:
    return _instance.shutdown(wait_s) if _instance else True


# ====================================================================== dry run
def run_campaign_dry_run(ctx: JobContext, payload: dict) -> dict:
    """Job `campaign_dry_run`. payload: {"campaign_id": int}. Runs every send-safety rule for each approved draft and
    sends nothing. Result: {"lines": [...], "would_send": n, "blocked": m}. A draft held back only by timing (window,
    daily limit, spacing, pause-all) still counts as would_send, with the timing as its reason."""
    cid = int(payload["campaign_id"])
    engine = ctx.engine
    settings = db.load_settings(engine)
    now = services.get_services().clock.now()
    with engine.connect() as c:
        drafts = c.execute(text("SELECT d.lead_id, d.kind, d.subject, lower(l.email) FROM drafts d JOIN leads l ON l.id = d.lead_id "
                                "WHERE d.campaign_id=:c AND d.status='approved' AND d.approved=1 ORDER BY d.id"), {"c": cid}).all()
    lines: list[dict] = []
    ctx.progress(0, len(drafts), "Checking approved drafts", force=True)
    for i, (lead_id, kind, subject, email) in enumerate(drafts, 1):
        ctx.raise_if_cancelled()
        with engine.connect() as c:
            existing = c.execute(text("SELECT state FROM messages WHERE campaign_id=:c AND lead_id=:l AND kind=:k"), {"c": cid, "l": lead_id, "k": kind}).scalar()
        if existing and existing != "queued":
            decision, reason = "blocked", f"Already {existing.replace('_', ' ')}; it will not be sent again."
        else:
            r = outbox.check_draft(cid, lead_id, kind, now, settings, engine)
            if r.ok:
                decision, reason = "would_send", None
            elif r.reason in TIME_REASONS:
                decision, reason = "would_send", f"Not right now: {r.detail}"
            else:
                decision, reason = "blocked", r.detail
        lines.append({"lead_id": lead_id, "email": email, "subject": subject, "decision": decision, "reason": reason})
        ctx.progress(i, len(drafts), f"Checked {i} of {len(drafts)}")
    with engine.begin() as c:
        c.execute(text("UPDATE campaigns SET dry_run_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=:i"), {"i": cid})
    would = sum(1 for ln in lines if ln["decision"] == "would_send")
    return {"lines": lines, "would_send": would, "blocked": len(lines) - would}
