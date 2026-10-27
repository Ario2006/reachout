# State (resume here)

Updated: 2026-10-28. Full plan: `docs/PARALLEL_PLAN.md`.

## Done
- [x] Milestones 1, 1b, 2, 2b (crawler, lead quality, send-safety core, benchmarks)
- [x] Phase 0: testing policy (scripts, network guard, template DB, markers), migration 005, ports and fakes, service registry, footer rule, SSE event types, API schemas and 501 router stubs, campaign CRUD, frontend placeholders and query keys, regenerated TS client

- [x] Phase 1 merged (reviewed, blockers fixed, gate green): B compose (m3), C sending (m4), D tracking (m5). Gate: 910 py + 191 fe tests.

- [x] Phase 2: follow-ups (E1 backend, E2 UI + template picker), F1 polish (palette, a11y, motion, window state, 5,000-row perf), e2e scenario with fakes, Playwright green, docs updated. Results: `docs/ACCEPTANCE.md`.

## In progress
- [ ] Waiting for the user's approval to package (F2: `scripts/build-mac.sh`, unsigned .app/.dmg)

## Next
1. F2 packaging after the user's go (use Node 24)
2. User's own first real send: test to self, dry run, start at cap 25

## Decisions
- 2b is complete; not redone. A is not dispatched.
- Footer is applied at send time (`footer.ensure_footer`), not stored in drafts. Internship may clear it; service and investor cannot.
- Résumé attachment is per campaign (spec 4.2), set in the Campaigns UI, not in Compose (small deviation from ui-migration-spec 7.4).
- Migrations are written only by the lead. Generated API files conflict at merge: the lead regenerates.

## Known gaps
- Backend lint: `ruff` is not installed (not in the specs); `test-fast.sh` and `test-all.sh` use it automatically once present.
- Node on this machine is v25.9; the repo pins 24 (`.nvmrc`, `engines`). Tests pass; use `nvm use` before packaging.
- 2b leftovers (P2, cut unless time remains): browser tier unverified with real Chromium, positive MX not persisted across runs, score ordering vs label.

## Baseline (before Phase 0 changes)
Backend 615 tests 14.4 s serial; frontend 138 tests 4.7 s. After: backend 7 s with `-n 2`, full gate about 20 s.

## Review follow-ups carried forward
- Dashboard charts are plain SVG (Recharts is named in the spec, no approval needed): swap in F1 if worthwhile.
- IMAP polls INBOX only. Real SMTP/IMAP/Electron tray never exercised against live servers (by design: no real email).
- Slow property tests are marked `slow`; `test-fast.sh` skips them, `test-all.sh` runs them (gate ~55 s).
- Never exercised against live services: SMTP/IMAP to Gmail, Electron tray/hide/quit during a real send, the browser tier. Dashboard bars are not keyboard-focusable (sr-only table instead). Dark-mode input borders are below 3:1.
- Changing `imap_poll_minutes` needs a worker restart.
