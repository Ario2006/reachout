# Architecture

Reachout is a local macOS desktop app. Nothing leaves the machine except your outgoing email and
polite web requests. This file describes the moving parts as built (through Milestone 2).

```
┌──────────────────────── Electron main process ────────────────────────┐
│ window · menu · splash · security · BackendSupervisor                 │
└───────┬───────────────────────────────────────────────┬───────────────┘
        │ preload bridge (contextBridge, no Node)       │ spawns + supervises
┌───────▼─────────────┐   HTTP + SSE, 127.0.0.1    ┌────▼──────────────┐   ┌──────────────────┐
│ Renderer            │──── bearer token ─────────▶│ FastAPI (uvicorn) │   │ Worker           │
│ React + TypeScript  │◀─── /events (SSE) ─────────│ thin routers      │   │ APScheduler      │
└─────────────────────┘                            └────┬──────────────┘   │ job runner       │
                                                        └──── SQLite (WAL) ─┴──────────────────┘
                                                                     │
                                                               Ollama (localhost:11434)
```

## Processes

| Process | Started by | Role |
| --- | --- | --- |
| Electron main | `scripts/run.sh` | Window, menu, splash, security, supervises the two Python processes |
| Renderer | Electron | React UI. Sandboxed: no Node, only the preload bridge |
| API (`backend.api.main:create_app`) | Electron | Thin HTTP layer over `backend/core/*` |
| Worker (`backend.worker`) | Electron | Heartbeat, job runner, event-log pruning (later: send queue, IMAP, follow-ups) |

The API and worker never talk to each other directly. They share state through SQLite.

## Repository layout

```
backend/
  core/        UI-agnostic logic: config, db, models, secrets, llm, health, jobs, events, job_handlers,
               planner, discovery, crawler, extractor, validator, leads, exports, pipeline, domains
    migrations/  numbered .sql files, applied in order (001_init, 002_jobs_events, 003_lead_pipeline)
  api/         FastAPI app factory, security middleware, schemas, routers/
  worker.py    background process entry
  cli.py       developer tool: run the pipeline from a terminal
  procutil.py  parent-death watchdog
desktop/
  electron/    main.ts, preload.ts, backend-supervisor.ts, menu.ts, splash.ts, csp.ts
  shared/      bridge.ts: the main/preload/renderer contract
  src/
    app/         shell, sidebar, command palette, router gate
    features/    setup/ (wizard), settings/ (7 tabs), account/, discover/, leads/
    components/  ui/ (shadcn) and small shared components
    lib/         api/ (generated types + typed client + hooks), sse.ts, theme.ts, stores
  e2e/         Playwright (Electron) tests
scripts/       setup.sh, run.sh, dev.sh
tests/         pytest (backend)
```

`backend/core` has no UI imports and is never rewritten for the UI's sake.

## Security model

- The API binds to `127.0.0.1` on a random free port chosen by Electron each launch.
- Every request needs `Authorization: Bearer <token>`. The token is random per launch, passed to
  the backend by environment variable and to the renderer through the preload bridge. It is
  compared in constant time. Query-string tokens are rejected.
- The `Host` header must be loopback (DNS-rebinding guard) and any `Origin` must be the renderer's
  origin (`reachout://app`, or the Vite dev server in development). CORS allows only that origin.
- No `/docs` or `/openapi.json` at runtime. The schema is exported by script.
- Secrets: the Gmail App Password lives in the macOS Keychain. API responses only ever say whether
  one is set. A test asserts no response contains it.
- Renderer: `contextIsolation`, `sandbox`, no `nodeIntegration`, strict CSP (set by the
  `reachout://` protocol handler), navigation denied, `window.open` denied except `http(s)` links
  opened in the default browser, all permission requests denied.
- Safety rules (caps, delays, windows, suppression, approval) are enforced in the backend. The UI
  may display them but cannot bypass them.
- Suppression: bounce and opt-out entries cannot be removed from the UI. Only manually added entries
  can, with a typed confirmation that is also checked server-side.

## Jobs and live updates

Long work is a row in `jobs` (`queued → running → succeeded | failed | cancelled`).

1. The API inserts a job (`POST /setup/pull-model`, later `/discovery/run`, …).
2. The worker polls `jobs` every second, claims the oldest queued job atomically, and runs the
   handler registered for its `kind` (`backend/core/job_handlers.py`) on a small thread pool.
3. Handlers report progress through a `JobContext` and check for cancellation cooperatively.
4. Every state or progress change is appended to `event_log` (throttled).
5. `GET /events` tails `event_log` and streams Server-Sent Events. The renderer reads it with
   `fetch` (not `EventSource`, which cannot send an `Authorization` header), resumes from
   `Last-Event-ID` after a reconnect, and feeds a small Zustand store.

`event_log` is a table rather than an in-memory queue because the producer (worker) and the
consumer (API) are different processes. It is pruned hourly (newest 2,000 kept).
A worker that crashed mid-job leaves it `running`; on start, those jobs are marked `failed`.

## The lead pipeline

`pipeline.run_discovery` is the job handler for `discovery_run`. One run is a pipeline of small, separately
testable modules in `backend/core`:

```
request ──planner──▶ Plan ──discovery──▶ candidate sites ──crawler──▶ pages ──extractor──▶ lead drafts
 (LLM, 1 call)     (editable)  ddgs + list pages        robots, pacing       emails, people      │
                                + OpenStreetMap                                                  ▼
                                                              validator (syntax, MX) ──leads.ingest──▶ SQLite ──▶ `lead` events
```

- **Planner** asks the model for a plan as JSON (structured output, validated by Pydantic). If the model
  is unreachable or returns junk twice, a deterministic template plan is used and labelled as such.
  Model queries about job listings are dropped, and template queries (company pages and "top N" lists)
  are always added, because job-board results were the main source of bad leads.
- **Discovery** runs the queries through `ddgs` (or a local SearXNG), applies the blocklist, ranks one
  candidate per registrable domain, and optionally follows list pages ("Top 50 AI startups in
  Bangalore") to the companies they link to. Service mode can also query OpenStreetMap.
- **Crawler** fetches the homepage, the search hit, keyword-matched links and common paths, up to the page
  cap. robots.txt follows RFC 9309 (4xx = no rules, 5xx or unreachable = disallow). Requests to one host
  are paced, responses are capped at 2 MB, and private/loopback hosts are refused.
- **Extractor** finds addresses in `mailto:` links, Cloudflare-protected links and visible text (with
  `[at]`/`[dot]` de-obfuscation), rejects image-like, placeholder and automated ones, and keeps only
  addresses on the site's own domain. It calls the model for people only on pages that look like team
  pages, never on job listings or portfolio/news/list pages, and only keeps a name that literally appears
  on the page. A person is only `personal` when a name on the page matches the address; a guess is only made
  for a target-role person listed on the site's own team page whose role is a plain title.
- **Validator** checks syntax and that the domain has an MX record (cached, retried once).
- **Leads** stores everything with its source URL and a context snippet taken from the page. Suppressed
  addresses are skipped, duplicates are ignored, and a published address upgrades a guessed one.

If a run finishes short of its target (default 30 leads) it **widens**: it searches with fresh wording
(`planner.more_queries`), follows more list pages, and crawls only sites it has not seen, for up to 4 rounds, about
12 minutes, or the site limit. Each site keeps at most 5 leads (`select_best` ranks them for the kind of outreach),
because one large company can otherwise contribute dozens of regional mailboxes.

Sites are processed a few at a time (default 4). Each site is crawled, read, validated and stored as a unit,
so leads appear in the UI as soon as their site finishes. Only one model call runs at a time (a process-wide
lock), and a run stops at 25 minutes. Cancelling keeps everything found so far.

The UI follows a run through four SSE event types: `run` (stage and counters), `run_domain`, `run_log`, and
`lead`. State is also in the database (`search_runs`, `run_domains`, `run_log`), so a reload rebuilds the
screen.

## Supervision

Electron picks the port and token, spawns uvicorn and the worker from `.venv`, and polls `/health`
behind a splash screen. A crashed process is restarted (up to 3 times a minute). After that the app
shows an error screen with Try again, View logs, and Quit. Both processes exit if Electron dies
(they watch their parent PID), and quitting stops them with SIGTERM, then SIGKILL after 3 s.

## Types

`python -m backend.api.export_openapi` writes the schema; `openapi-typescript` turns it into
`desktop/src/lib/api/schema.d.ts`. `npm run gen:api` does both, and `npm run gen:api -- --check`
fails if the committed types are stale. The typed client (`lib/api/client.ts`) derives request and
response types from those generated paths, so a backend change that breaks the UI fails `tsc`.

## Conventions

- TypeScript strict, `noUncheckedIndexedAccess`; ESLint forbids `any`.
- Tailwind v4 (CSS-first, `@tailwindcss/vite`), shadcn/ui on Radix, `tw-animate-css`.
- Node 24 LTS (`.nvmrc`, `engines`).
- New milestones are vertical slices: backend logic + endpoints + React screen + tests.
