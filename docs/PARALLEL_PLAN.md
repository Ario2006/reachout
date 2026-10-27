# Parallel plan: finishing Reachout (milestones 3 to 7)

Status of this plan: **complete except F2 packaging, which waits for the user's approval** (see docs/STATE.md). Specs: `app-spec.md`, `ui-migration-spec.md`.

## Audit result

| Milestone | State | Evidence |
| --- | --- | --- |
| 1, 1b, 2 | done | wizard, settings, lead pipeline, Discover, Leads |
| 2b (crawler, lead quality, send-safety core) | **done, not redone** | steps C to H committed; `docs/m2b-benchmarks.md`: 200 domains in 140 s (target 600 s), peak RSS 299 MB, LLM calls per 100 leads 39.5 |
| 3 to 7 | not started | no templates, drafts, sender, tracker or screens exist |

Leftover 2b items (browser tier unverified against real Chromium, positive MX not persisted, score ordering) are P2 and **cut** unless time remains.

## Contracts committed in Phase 0 (do not change; request changes from the lead)

| Contract | Where |
| --- | --- |
| All remaining tables and columns, plus DB guarantees (editing an approved draft withdraws approval; `approved` flag cannot disagree with `status`; one incoming email recorded once; one message per campaign, lead and kind) | `backend/core/migrations/005_outreach.sql` |
| New settings: `bounce_pause_pct`, `bounce_pause_min_sends`, `imap_poll_minutes` | `backend/core/models.py` |
| Ports: `Mailer`, `ImapClient`, `LlmClient`, `PageCache`, `Clock`, mail errors | `backend/core/ports.py` |
| In-memory fakes | `backend/core/fakes.py` |
| Service registry (`get_services()`, `services.use(services.fakes())` in tests) | `backend/core/services.py` |
| Opt-out footer rule (implemented): `footer.ensure_footer(body, settings, mode)`, applied at send time | `backend/core/footer.py` |
| SSE events `draft`, `campaign`, `message`, `inbox`, `sending`; emit with `event_types.publish` | `backend/core/event_types.py`, `desktop/src/lib/event-types.ts` |
| API schemas per area; routers answer 501 until built; campaign CRUD is real | `backend/api/schemas_{compose,campaigns,inbox}.py`, `backend/api/routers/*` |
| Worker hooks: each of `sender`, `inbox_poller`, `followups` exposes `register(sched, engine)`; job kinds `drafts_generate`, `inbox_check`, `campaign_dry_run` are registered | `backend/worker.py`, `backend/core/job_handlers.py` |
| Frontend: routes and placeholder pages, query-key prefixes, SSE routing that invalidates by prefix, type aliases | `desktop/src/lib/api/{keys,types}.ts`, `desktop/src/lib/events.tsx`, `desktop/src/features/*/ *-page.tsx` |
| Test factories (`tests/factories.py`), network guard, template DB, `.eml` fixture folder | `tests/` |

Cross-task coupling is **only through tables and these contracts**: B writes `drafts`; C reads approved drafts, writes `messages`, `campaigns`; D reads `messages`, writes `events`, `suppression`, `leads.status`; C reads `events` (bounce rate); E reads and writes `messages`.

## Tasks, owners and files (no overlap)

Each task works in its own worktree: `scripts/new-worktree.sh <name> <branch>`.

### A. 2b crawler: DONE (no dispatch)

### B. Compose backend and draft review UI (branch `m3-compose`)
Owns: `backend/core/{drafts,composer,personaliser}.py`, `backend/api/routers/compose.py`, `backend/api/schemas_compose.py`, `tests/test_{compose,drafts,personaliser}*.py`, `tests/fixtures/compose/`, `desktop/src/features/compose/**`, `desktop/src/lib/api/hooks-compose.ts`, `desktop/src/features/leads/bulk-bar.tsx` (add "Generate drafts for selection" only).
Done when:
- Three starter templates seeded (under 120 words, plain text); Jinja2 rendering that reports missing fields; profile fields CRUD.
- Hybrid opener: LLM writes only `custom_opener`. Guardrails: at most 35 words, grounded in the stored context snippet, no invented numbers, names or URLs, banned phrases rejected; retry once, then neutral fallback and status `needs_review` with a flag. Hypothesis test: no accepted opener contains a number, URL or capitalised name absent from the snippet.
- `drafts_generate` job with progress and `draft` events; flags for guessed address, no context, fallback opener; lead status moves to `drafted` / `approved`.
- Edit, approve, reject, bulk approve (unflagged only by default), regenerate opener; an approved draft cannot be sent after an edit (DB trigger already; API test proves it).
- UI: template list and editor with merge-field chips and live preview, LLM-written sentence highlighted; draft review master-detail; keyboard `A` `R` `J` `K`; "Approve all unflagged"; test that 50 drafts can be approved by keyboard only; error states with retry; skeletons.
- T2 green: `scripts/test-module.sh compose drafts personaliser`.

### C. Mailer, sender worker, Campaigns UI (branch `m4-sending`)
Owns: `backend/core/{sender,mailer,outbox,campaigns}.py`, `backend/worker.py`, `backend/api/routers/campaigns.py`, `backend/api/schemas_campaigns.py`, `tests/test_{sender,mailer,outbox,campaigns,safety}*.py`, `desktop/src/features/campaigns/**`, `desktop/src/lib/api/hooks-campaigns.ts`, `desktop/src/app/sidebar.tsx` (footer: sends vs cap, pause-all switch), `desktop/electron/**` (tray, close-hides-while-sending, quit pauses via `POST /sending/pause-all`).
Done when every row of the safety matrix below marked C passes, and:
- `SmtpMailer` (smtplib over 465, Keychain password never logged, plain text, MIME with attachment, Message-ID generated before the call, `In-Reply-To`/`References` for follow-ups, From display name).
- Start enqueues messages from approved drafts with a body snapshot; `not_before` honoured; test send to self; dry-run job; pause, resume, pause-all; `caffeinate` only while a run is active and the UI says so.
- Graceful shutdown: SIGTERM finishes or releases the in-flight claim and leaves campaigns paused (`paused_by shutdown`); restart resumes.
- Campaigns UI: list, detail, readiness checklist, buttons, progress bar with next-send countdown, auto-pause banner, needs-confirmation list with confirm and retry.
- T2 green: `scripts/test-module.sh sender mailer outbox campaigns safety`.

### D. IMAP tracker, Inbox and Dashboard (branch `m5-tracking`)
Owns: `backend/core/{tracker,inbox_poller,threads,metrics}.py`, `backend/api/routers/{inbox,dashboard}.py`, `backend/api/schemas_inbox.py` (E extends later), `tests/test_{tracker,inbox,metrics}*.py`, `tests/fixtures/eml/**`, `desktop/src/features/{inbox,dashboard}/**`, `desktop/src/lib/api/hooks-inbox.ts`.
Done when (all against `.eml` fixtures and `FakeImap`, never Gmail):
- Reply match by `In-Reply-To`/`References`, fallback sender and subject; auto-reply detection (`Auto-Submitted`, `X-Autoreply`, `Precedence: bulk`, subject patterns) recorded as `auto_reply`; DSN parsing with recipient and 5.x.x code, hard bounce marks lead `bounced` and suppresses; opt-out phrases in the first lines mark `opted_out`, suppress and flag for confirmation.
- Idempotent polling (re-delivery creates nothing new), UID validity reset, login failure recorded and shown, `inbox` events, `inbox_check` job, 10-minute schedule from settings.
- Metrics per spec 4.4 with exact definitions in the module docstring; attention list; activity feed.
- Inbox UI: grouped conversations, auto-replies distinct, quick statuses, Open in Gmail link, opt-out one-click confirm, Check now with last-checked time. Dashboard: KPI cards, funnel, sends-per-day chart, attention list, live feed; the "opens are not tracked" note.
- T2 green: `scripts/test-module.sh tracker inbox metrics`.

### Phase 2 (after B, C, D are merged)
- **E1 follow-ups backend** (`m6-followups-api`): `backend/core/followups.py`, `routers/followups.py`, `tests/test_followups*.py`. Schedules up to 2 follow-ups, same thread, skipped on reply, bounce, opt-out or blocking status, goes through the same outbox checks; approval toggle uses `drafts.kind`.
- **E2 follow-ups UI** (`m6-followups-ui`): new files only under `desktop/src/features/campaigns/followups-*.tsx` plus one mount line.
- **F1 polish** (accessibility and performance audit, shortcuts, animations, command palette actions, 5,000-row table check): touches UI files across features, runs alone.
- **F2 packaging** (`scripts/build-mac.sh`): **stops for the user's approval first**.
E1 and E2 may run in parallel; F1 and F2 may run in parallel after them.

## Safety matrix (backend-enforced, each with a named test)

| Rule | Enforced in | Owner | Test |
| --- | --- | --- | --- |
| Approval required; sent text equals approved text | `outbox.enqueue` and `outbox.check` (reason `not_approved`) | C | `test_safety` |
| Daily cap, 100/day ceiling | `outbox.effective_cap`, atomic claim | done | `test_outbox` (+ hypothesis, C) |
| Send window, recipient time zone | `outbox.check` / `tz` | done | `test_outbox`, `test_tz` |
| Jitter 60 to 180 s between sends | sender sets `campaigns.next_send_at` | C | `test_sender` (FakeClock) |
| Per-company limits | claim | done | `test_outbox` |
| Bounce auto-pause (above 5% after 20 sends) with reason | sender, before every claim | C | `test_safety` |
| Suppression at send time, including follow-ups | claim (SQL) | done | `test_outbox`, `test_safety` |
| Never the same email twice, even after a crash between `sending` and `sent` | unique index, `recover_stale` | done | `test_outbox`; C adds a sender-level crash test |
| Opt-out footer on every email (test send and follow-ups too) | sender calls `footer.ensure_footer` | C | `test_safety` |
| SMTP login failure stops the run, no retry loop | sender | C | `test_sender` |
| Replied, bounced, opted-out, closed leads get nothing further | `BLOCKING_LEAD_STATUS`; D sets the status | done, D | `test_outbox`, `test_tracker` |
| Guessed addresses excluded by default | `outbox.check` | done | `test_outbox` |
| No secret in logs or API responses | all | C | `test_safety` (log capture), `test_api` (exists) |

## Conventions for every subagent
- Read narrowly: grep first, then ranges. Run quietly, show only tails. No full-file rewrites, no refactors of working code, no docs or tests that add no value.
- Tests with the code, using `tests/factories.py`, the fakes and `services.use(services.fakes())`. No network, SMTP, IMAP, DNS or Ollama (the guard fails the test). Hypothesis for cap, window, throttle and extraction logic.
- Inner loop `scripts/test-fast.sh`; before reporting, `scripts/test-module.sh <names>` only. Never run the full suite or Playwright.
- No new dependency without the lead. No migrations: ask the lead. Do not edit files you do not own; `openapi.json` and `schema.d.ts` are generated (`npm run gen:api` in `desktop/`), commit them with your branch, the lead regenerates on conflict.
- Report in at most 25 lines: task, branch, T2 result, duration, deviations, open issues. Commit to your branch only; never touch main.

## Merge order and gates
B, then C, then D (plan order; files are disjoint, the only expected conflicts are the two generated API files). For each: independent review subagent (diff vs specs, safety matrix, contracts, T2 report; does not rerun the suite), blockers fixed, then the lead merges and runs `scripts/test-all.sh`. Red means fix or revert before the next merge. Then E1 and E2, F1, F2, then the final pass (end-to-end scenario with fakes, acceptance criteria with measured numbers, README and ARCHITECTURE).
