# State (resume here)

Updated: 2026-10-28. Full plan: `docs/PARALLEL_PLAN.md`.

## Done
- [x] Milestones 1, 1b, 2, 2b (crawler, lead quality, send-safety core, benchmarks)
- [x] Phase 0: testing policy (scripts, network guard, template DB, markers), migration 005, ports and fakes, service registry, footer rule, SSE event types, API schemas and 501 router stubs, campaign CRUD, frontend placeholders and query keys, regenerated TS client

## In progress
- [ ] Waiting for the user's "go" to dispatch B, C, D (3 parallel subagents)

## Next
1. B compose, C sending, D tracking (parallel, one worktree each)
2. Review subagent per branch, merge in order B, C, D, `scripts/test-all.sh` after each
3. E1/E2 follow-ups, F1 polish, F2 packaging (needs user approval)
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
