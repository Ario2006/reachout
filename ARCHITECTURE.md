# Architecture

Reachout is a local macOS desktop app. Nothing leaves the machine except your outgoing email and
polite web requests. This file describes the moving parts as built (through Milestone 1b).

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
  core/        UI-agnostic logic: config, db, models, secrets, llm, health, jobs, events, job_handlers
    migrations/  numbered .sql files, applied in order (001_init, 002_jobs_events)
  api/         FastAPI app factory, security middleware, schemas, routers/
  worker.py    background process entry
  procutil.py  parent-death watchdog
desktop/
  electron/    main.ts, preload.ts, backend-supervisor.ts, menu.ts, splash.ts, csp.ts
  shared/      bridge.ts: the main/preload/renderer contract
  src/
    app/         shell, sidebar, command palette, router gate
    features/    setup/ (wizard), settings/ (7 tabs), account/
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
