# Acceptance run (final pass)

Run on the M3 / 8 GB Mac, 2026-10-28. "Fakes" means the in-memory mailer, IMAP, LLM and clock; nothing real was sent.

| Criterion (spec) | Result | Evidence |
| --- | --- | --- |
| End-to-end loop: leads, draft, approve, dry run, test send, send, reply, bounce, dashboard, follow-up | PASS (fakes) | `tests/test_e2e_scenario.py` |
| No email to a suppressed address; never twice, even after a crash between `sending` and `sent` | PASS | `test_outbox`, `test_safety` (hypothesis, random interleavings + crash) |
| Caps, 100/day ceiling, send window, jitter, per-company limits | PASS | `test_outbox`, `test_safety` |
| Bounce auto-pause above 5% after 20 sends; SMTP login failure stops the run | PASS | `test_safety`, `test_sender` |
| Opt-out footer on every email (initial, follow-up, test send) | PASS | `test_safety`, e2e scenario |
| Approval required; editing after approval withdraws it | PASS | DB trigger + `test_contracts`, `test_drafts`, `test_safety` |
| Drafts never contain facts absent from the snippet | PASS | hypothesis guardrail test with an independent oracle |
| IMAP reply / bounce / auto-reply / opt-out matching on `.eml` fixtures | PASS | `test_tracker`, `test_inbox` (35 fixtures) |
| Request without the bearer token rejected | PASS | `test_api`; live check: 401 without token |
| Closing mid-send keeps sending; quit pauses cleanly | Code + typecheck only | Electron tray/quit logic not exercised in a real send (by design: no real email) |
| Crawl 200 domains < 10 min | PASS (earlier, real network) | 140 s, `docs/m2b-benchmarks.md` |
| Backend peak RSS < 1.5 GB | PASS | crawl 299 MB peak; idle API + worker 143 MB |
| Leads table smooth at 5,000 rows | PASS (DOM/render level) | Playwright: 5,003 rows render as a window; vitest: 26 DOM rows, 1 row re-renders per selection. No FPS measurement. |
| Draft review: 50 approvals by keyboard | PASS | vitest |
| Playwright smoke, lifecycle, pipeline | PASS (20 passed, 1 live spec skipped by design) | final run; one timing-sensitive 5,003-row check failed once under machine load (p95 35 ms vs 34), 24 ms when re-run |
| >= 90% line coverage on safety/sending/tracking/extraction modules | PASS | outbox 96.7, tz 92.6, footer 100, sender 95.7, mailer 91.7, personaliser 99.1, tracker 94.0, inbox_poller 97.1, followups 96.6, extractor 94.1, heuristics 95.1, validator 100 (total backend 94.3) |
| >= 30 leads from "AI/ML internship openings in Bangalore" | Not re-run | needs live web and Ollama; last measured in `docs/m2-yields.md` |
| Idle Electron + backend + worker < 600 MB | PASS | about 258 MB (packaged app, 30 s after launch) |
| Memory with a 3B model loaded < 6 GB | Not measured | Ollama not launched in this pass |
| Packaged unsigned .app/.dmg (arm64) launches, 7 screens, /health 200 via bundled backend, quit leaves no children | PASS | `scripts/build-mac.sh`; .app 428 MB, .dmg 176 MB; idle Electron + api + worker about 258 MB |
| Backend lint | PASS | ruff, correctness rules (`ruff.toml`), part of `test-all.sh` |
| Real SMTP/IMAP against Gmail | Not run | needs your account; see "Before the first real send" |

## Test time

| | Before | After |
| --- | --- | --- |
| Backend | 615 tests, 14.4 s serial | 958 tests, 48 s in the gate (7 s without the `slow` hypothesis safety properties; `-n 2`) |
| Frontend | 138 tests, 4.7 s | 235 tests |
| Inner loop (`test-fast.sh`) | n/a | changed files only, `slow` skipped |
| Full gate (`test-all.sh`, no Playwright) | n/a | about 65 s |

## Before the first real send
1. Settings > Account: Gmail address + App Password (a separate cold-outreach account is recommended).
2. Settings > Crawling: put a URL or email in "crawler contact".
3. Campaign > **Send test to myself**, check the footer and threading in Gmail, then **Dry run**, then Start with the default cap of 25.
