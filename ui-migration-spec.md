# ui-migration-spec.md — Replace Streamlit with a native-feeling desktop UI

This file **amends `app-spec.md`**. Everything in `app-spec.md` about behaviour, data model, safety limits, legal rules and acceptance criteria still applies. Only the UI layer and process model change. Where the two files conflict, this file wins for anything about UI, packaging and process layout.

## 1. Goal

Replace the Streamlit UI with a polished, professional desktop app (Mac-first) that feels like Linear / Superhuman / Apple Mail rather than a data-science dashboard. Keep all existing Python logic. No paid services, fully local, must run comfortably on an M3 MacBook with 8 GB unified memory alongside Ollama.

## 2. Architecture

```
┌────────────────────────── Electron shell ──────────────────────────┐
│  Renderer: React + TypeScript + Vite                               │
│  (Tailwind, shadcn/ui, TanStack Query/Table, Recharts)             │
└───────────────▲────────────────────────────────────────────────────┘
                │ HTTP + SSE on 127.0.0.1 (random port, per-launch token)
┌───────────────┴──────────────┐    ┌───────────────────────────────┐
│ FastAPI backend (Python)     │    │ Worker process (Python)       │
│ thin routers over app/core/* │◄──►│ send queue, IMAP poller,      │
│                              │    │ follow-ups, crawl jobs        │
└───────────────┬──────────────┘    └───────────────┬───────────────┘
                └──────────── SQLite (WAL mode) ─────┘
                                  │
                              Ollama (localhost:11434)
```

### Decision: Electron, not Tauri

Use **Electron** (via `electron-vite`, TypeScript). Reasons: the most widely used desktop stack, no Rust toolchain, and simple to spawn and supervise Python child processes. The ~250 MB overhead is acceptable because the LLM, not the shell, is the real memory cost. Keep the renderer free of Electron-specific APIs (go through a small `preload` bridge) so the UI could be moved to Tauri later.

### Keep the Python core untouched

`app/core/*` (config, db, models, llm, planner, discovery, crawler, extractor, validator, personaliser, mailer, tracker, scheduler, exports) is UI-agnostic and **must not be rewritten**. Only add:

- `app/api/` — FastAPI app, routers, Pydantic schemas, auth middleware, SSE helpers.
- Small adapters where core functions currently call `streamlit` (progress callbacks, session state, caching). Replace those with plain callbacks/events written to a `jobs` table. Remove every `import streamlit` from `core/`.

## 3. Repository layout

```
reachout/
  backend/            # existing app/ moved here (core/, worker.py) + api/
  desktop/            # Electron + React
    electron/         # main.ts, preload.ts, backend-supervisor.ts, menu.ts
    src/
      app/            # routes, layout, providers
      features/       # leads/, discover/, compose/, campaigns/, inbox/, dashboard/, settings/
      components/ui/  # shadcn components
      lib/            # api client (generated), sse hook, formatters
    package.json  electron.vite.config.ts  tailwind.config.ts
  scripts/            # setup.sh, run.sh, dev.sh, build-mac.sh
  tests/              # pytest (backend), vitest (frontend)
```

## 4. Backend API (FastAPI)

- Bind to `127.0.0.1` on a free port chosen by Electron; **every request needs `Authorization: Bearer <token>`**, a random token generated per launch and passed to the backend via environment variable. Reject all other origins; CORS allows only the renderer origin. No secret ever returned in an API response (the Gmail App Password stays in Keychain; the API only reports whether it is set).
- Generate OpenAPI and a typed TypeScript client with `openapi-typescript` so frontend and backend types cannot drift.
- Long jobs (search/crawl, draft generation, sending, IMAP check) are **job records** in SQLite: `jobs(id, kind, state, progress, total, message, error, created_at, finished_at)`. The API exposes `POST /jobs/...` to start, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, and **`GET /events` (Server-Sent Events)** that streams job progress, new leads, status changes and replies so the UI updates live without polling.

Endpoint groups (all JSON):

| Group        | Endpoints                                                                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Health/setup | `GET /health`, `GET /setup/status` (Ollama running? model present? Gmail SMTP/IMAP OK?), `POST /setup/pull-model` (progress via SSE), `POST /setup/test-gmail`                  |
| Discovery    | `POST /discovery/plan` (request → editable plan), `POST /discovery/run` (plan → job), `GET /discovery/runs`                                                                     |
| Leads        | `GET /leads` (filter, sort, search, paginate), `PATCH /leads/{id}`, `POST /leads/bulk` (delete, status, tag), `POST /leads/import-csv`, `GET /leads/export.csv`                 |
| Compose      | CRUD `/templates`, `POST /drafts/generate` (job), `GET /drafts`, `PATCH /drafts/{id}` (edit/approve/reject), `POST /drafts/bulk-approve`, `POST /drafts/{id}/regenerate-opener` |
| Campaigns    | CRUD `/campaigns`, `POST /campaigns/{id}/test-send`, `/dry-run`, `/start`, `/pause`, `/resume`, `GET /campaigns/{id}/stats`                                                     |
| Inbox        | `GET /threads` (sent, replies, bounces grouped per lead), `POST /inbox/check-now`, `POST /leads/{id}/status`                                                                    |
| Dashboard    | `GET /metrics?campaign_id=&range=`                                                                                                                                              |
| Settings     | `GET/PUT /settings`, `PUT /settings/gmail` (writes to Keychain), `GET/POST/DELETE /suppression`                                                                                 |

All safety rules from `app-spec.md` (daily cap, delays, send window, ceiling of 100/day, bounce auto-pause, suppression check at every send, approval required) are enforced **in the backend**. The UI may display them but can never bypass them.

## 5. Electron shell responsibilities

- On launch: pick a free port, generate the token, spawn `uvicorn` and `worker.py` from the project's virtualenv (dev) or bundled binaries (packaged), poll `/health`, then show the window. Show a clean splash screen with status ("Starting backend…", "Checking Ollama…") instead of a blank window.
- Supervise child processes: restart on crash (max 3 times, then show an error screen with a "View logs" button); kill both on quit.
- **Quit/close behaviour:** if a campaign is sending, closing the window hides the app to the menu bar (tray icon with status and Pause/Quit) instead of quitting; explicit Quit asks for confirmation and pauses the campaign cleanly.
- Mac-native feel: `titleBarStyle: 'hiddenInset'`, a draggable custom title region, native application menu, standard shortcuts, `vibrancy` sidebar where it looks good, follow the system light/dark setting, remember window size and position.
- Security: `contextIsolation: true`, `nodeIntegration: false`, a strict Content-Security-Policy, deny all navigation and `window.open` except to open `http(s)` links in the default browser, no remote code loading.
- Single instance lock. Logs written to `~/Reachout/logs/`; menu item "Show logs".
- Provide `scripts/dev.sh` (hot-reloading renderer + backend with `--reload`) and `scripts/build-mac.sh` (package with `electron-builder` into an unsigned `.dmg`/`.app`, with the Python backend bundled via PyInstaller or a relocatable venv). **Packaging is the last milestone**; running from source with `scripts/run.sh` must work first.

## 6. Frontend stack and conventions

- React 18 + TypeScript (strict) + Vite, React Router, **Tailwind CSS + shadcn/ui** (Radix primitives), **lucide-react** icons, **TanStack Query** for server state, **TanStack Table** (with row virtualisation) for the leads table, **Recharts** for charts, **Zustand** only for small UI state, **react-hook-form + zod** for forms, **sonner** for toasts, **cmdk** for the command palette.
- Fonts: system font stack (SF Pro on Mac); monospace only where it helps (email addresses, IDs).
- Design language: calm, dense-but-airy, neutral grayscale with one accent colour, subtle borders, 8px grid, rounded-lg, light and dark themes, no gradients or heavy shadows. Consistent empty states, skeleton loaders, and inline error messages with a retry action. Never leave the user staring at a spinner without progress text.
- Accessibility: keyboard navigable everywhere, visible focus rings, `aria` labels on icon buttons, AA colour contrast.
- No component library other than shadcn/ui; no CSS-in-JS; no Redux.

## 7. Screens

Left sidebar navigation (collapsible): **Dashboard, Discover, Leads, Compose, Campaigns, Inbox, Settings**, with a footer showing Ollama status, today's sends vs cap, and a pause-all switch. `⌘K` opens a command palette (jump to a screen, "New search", "Check inbox now", "Pause sending", search leads by name/email/company).

1. **Dashboard** — KPI cards (leads, drafts awaiting review, sent today / cap, replies, reply rate, bounce rate), funnel chart, sends-per-day chart, "needs attention" list (bounce rate warning, drafts flagged `needs_review`, replies unread, follow-ups due), recent activity feed (live via SSE).
2. **Discover** — a large prompt box ("Describe who you want to reach…"), mode chips (Internship / Investor / Service), then an **editable plan card** (roles, industries, regions, queries as removable chips) with a Run button. While running: a live stepper (Searching → Crawling → Extracting → Validating) with counters, a streaming log, per-domain results appearing in a table as they are found, and a Cancel button. Finish with a summary and "Review N new leads".
3. **Leads** — virtualised table with columns: name, email (with confidence badge: personal / generic / guessed), role, company, source (link), status, last activity. Features: column show/hide, multi-sort, search, filter chips (status, mode, confidence, run), inline edit, bulk select (delete, add to suppression, move to campaign, set status), side drawer with full details (source snippet, timeline of sent/reply/bounce events, notes), CSV import and export (respecting current filters).
4. **Compose** — split view. Left: template list and profile fields (my summary, pitch, résumé attachment). Right: template editor with merge-field chips and live preview against a chosen lead. Below or in a tab: **Draft review queue** as a master-detail list (lead list on the left with flag badges, editable email on the right, buttons Approve / Reject / Regenerate opener, keyboard shortcuts `A`, `R`, `J`/`K`, bulk "Approve all unflagged"). Show a clear indicator of which sentence is LLM-generated.
5. **Campaigns** — campaign list with state badges; detail view with settings (daily cap with warnings above recommended values, send window, delay range, follow-up schedule), readiness checklist (Gmail OK, drafts approved, attachment size OK, test email sent), buttons **Send test to myself / Dry run / Start / Pause / Resume**, live progress bar with next-send countdown, and the auto-pause banner if bounce limits trip.
6. **Inbox** — conversation view grouped by lead: your sent email(s), their reply, bounce notices, auto-replies marked distinctly, quick status buttons (Interested, Not interested, Meeting booked, Close), "Open in Gmail" link, and a "Check now" button with last-checked time. Opt-out replies are highlighted with a one-click confirm.
7. **Settings** — tabs: Account (Gmail address, App Password stored in Keychain, send-as name, footer), Sending limits, AI (Ollama model dropdown from installed models, test generation), Crawling (limits, blocklist), Suppression list (search, add, remove with confirmation), Data (open data folder, export everything, delete everything), About/logs.
8. **First-run wizard** — full-screen stepper: Ollama installed/running → model pulled (progress bar) → Gmail connection tested (with instructions for creating an App Password and a note recommending a separate account) → your name, footer and legal notice acknowledgement → done.

## 8. Migration plan and revised milestones

**Starting point:** Milestone 1 of `app-spec.md` (skeleton, SQLite schema, Settings, Keychain secrets, first-run wizard) is already built with Streamlit. The original milestones 2-7 are **not built yet**. From here on, every milestone is a **vertical slice**: backend logic + API endpoints + the React screen + tests, delivered together. Do not build any new Streamlit pages.

Before starting, create a git tag `streamlit-m1` so the old version can always be restored.

After each milestone: run the tests, do a manual check in the running app, and summarise what works, what doesn't and any deviations, then wait for my "go".

| #      | Milestone                                  | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------ | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **1b** | **UI swap (replaces the Streamlit M1 UI)** | (a) Remove every `streamlit` import from `core/`; add the `jobs` table and event emitter; confirm existing tests still pass. (b) FastAPI layer with auth token, OpenAPI-generated TS client, SSE `/events`, and endpoints for health, setup status, model pull, Gmail test, settings, suppression. (c) Electron shell + React app: backend supervisor, splash screen, sidebar layout, light/dark theme, command palette, **first-run wizard** and **Settings** screens reaching full parity with the old Streamlit pages. (d) Delete all Streamlit code and dependencies; update `setup.sh` / `run.sh` / README. Other sidebar items appear disabled with a "coming soon" tag. |
| **2**  | **Lead pipeline**                          | Planner, discovery, crawler, extractor, validator, jobs + progress events (backend) with the **Discover** and **Leads** screens (editable plan, live progress, streaming results table, CSV import/export, lead drawer). Test with the three example requests in `app-spec.md` and report real yields and the share of `published_personal` emails.                                                                                                                                                                                                                                                                                                                            |
| **3**  | **Compose and review**                     | Templates, hybrid opener generation with guardrails, **Compose** screen and keyboard-driven draft review queue.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **4**  | **Sending**                                | Worker, caps, delays, send window, test send, dry run, pause/resume, bounce auto-pause, **Campaigns** screen, menu-bar behaviour while sending.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **5**  | **Tracking**                               | IMAP poller, reply/bounce/auto-reply/opt-out detection, suppression enforcement, **Inbox** and **Dashboard** screens.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **6**  | **Follow-ups**                             | Scheduling, threading, UI for follow-up settings and status.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **7**  | **Polish and packaging**                   | Shortcuts, animations, accessibility pass, window-state persistence, performance check at 5,000 leads, `build-mac.sh` producing an unsigned `.app`/`.dmg` with the Python backend bundled.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

Running from source with `scripts/run.sh` must work at every milestone; packaging is only done in milestone 7.

## 9. Acceptance criteria (UI-specific)

- `scripts/run.sh` opens the Electron app on a clean setup; no browser tab, no terminal interaction required after launch.
- Idle memory for Electron + backend + worker is under ~600 MB (Ollama excluded); with a 3B model loaded the whole system stays under ~6 GB.
- Typing a request in Discover shows an editable plan in under 5 seconds and live progress during the run; leads appear in the table as they are found.
- The Leads table stays smooth (60 fps scrolling) at 5,000 rows.
- Draft review supports approving 50 drafts using only the keyboard.
- Closing the window during a send keeps sending (menu-bar mode); quitting pauses the campaign cleanly and it resumes on next launch.
- A request without the correct bearer token is rejected (tested); the API is not reachable from other machines or other browser origins.
- No `streamlit` or `gradio` dependency remains after milestone 1b. Frontend has strict TypeScript with zero `any` in feature code, Vitest tests for key components and hooks, and a Playwright smoke test for the happy path (setup wizard skipped via env flag).

## 10. Working agreement

- Read this file and `app-spec.md` fully, inspect the current repository, and compare it with this plan. Then report what already exists, what will be reused, and what will change, **before writing code**. Wait for my "go".
- Do not rewrite working core logic for style reasons. If a core change is required, explain why first.
- Ask before adding any dependency not named here, and never add one that needs an API key or costs money.
- Keep `README.md` and a short `ARCHITECTURE.md` up to date as you go.
