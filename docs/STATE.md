# State (resume here)

Updated: 2026-10-28. Full plan: `docs/PARALLEL_PLAN.md`.

## Done
- [x] Milestones 1, 1b, 2, 2b (crawler, lead quality, send-safety core, benchmarks)
- [x] Phase 0: testing policy (scripts, network guard, template DB, markers), migration 005, ports and fakes, service registry, footer rule, SSE event types, API schemas and 501 router stubs, campaign CRUD, frontend placeholders and query keys, regenerated TS client

- [x] Phase 1 merged (reviewed, blockers fixed, gate green): B compose (m3), C sending (m4), D tracking (m5). Gate: 910 py + 191 fe tests.

## In progress
- [ ] Phase 2: E1 follow-ups backend, E2 follow-ups UI + template picker in campaign form

## Next
1. E1/E2 follow-ups (parallel), review, merge
2. F1 polish (a11y/perf audit, shortcuts, command palette actions, swap SVG charts for Recharts, 5,000-row check), F2 packaging (needs user approval)
4. Final pass: end-to-end scenario with fakes, acceptance numbers, README and ARCHITECTURE

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
- Campaign form has no template picker yet (E2 adds it).
- IMAP polls INBOX only. Real SMTP/IMAP/Electron tray never exercised against live servers (by design: no real email).
- Slow property tests are marked `slow`; `test-fast.sh` skips them, `test-all.sh` runs them (gate ~55 s).
