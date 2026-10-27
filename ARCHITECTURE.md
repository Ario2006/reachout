# Architecture

Reachout is a local macOS desktop app. Nothing leaves the machine except your outgoing email and
polite web requests. This file describes the moving parts as built (through follow-ups; packaging pending).

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
               planner, discovery, afetch, crawlplan, heuristics, analysis, extractor, llmqueue, engine, validator,
               scoring, leads, exports, pipeline, store, outbox, tz, slog, domains
    migrations/  numbered .sql files, applied in order (001_init … 004_engine)
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

`pipeline.run_discovery` is the job handler for `discovery_run`. It runs one asyncio event loop per run. Modules in
`backend/core`, each separately testable:

```
request ─planner─▶ Plan ─discovery─▶ candidate sites ─engine─▶ per-site crawl ─▶ analysis ─▶ drafts ─▶ validate ─▶ leads.ingest
(LLM, 1 call)   (editable) ddgs + lists  (async, 30 at once)  afetch + crawlplan  heuristics   merge,     MX, disposable   score, dedupe,
                            + OpenStreetMap                   cache→HTTP→browser  then LLM     trim       domains          sources, trace
```

- **Planner** asks the model for a plan as JSON (structured output, validated by Pydantic), with a deterministic
  template fallback. Job-listing queries are dropped; company-page and "top N" templates are always added.
- **Discovery** runs the queries through `ddgs` (or a local SearXNG), applies the blocklist, ranks one candidate per
  registrable domain, and can follow list pages to the companies they link to. Service mode can query OpenStreetMap.
- **Fetcher** (`afetch.AsyncFetcher`): one pooled `httpx.AsyncClient`, a global semaphore (30), and a per-host gate so
  a site sees one request at a time, 1-2 s apart or its `Crawl-delay` (up to 10 s; longer means the site is skipped).
  Waterfall: SQLite **page cache** (14 days, conditional requests, robots and 404s cached) → HTTP → optional
  Playwright browser (lazy, at most 2 pages, images and fonts blocked) for pages that arrive as an empty JavaScript
  shell. Timeouts 5 s connect / 15 s total, 2 MB cap, at most 2 retries with backoff, jitter and `Retry-After`, a
  per-domain circuit breaker, DNS failures remembered, private/loopback hosts refused. robots.txt follows RFC 9309.
- **Planning a site** (`crawlplan`): homepage first, then links scored by page type and the run's mode, the
  `sitemap.xml` only when the homepage links little, and at most three blind path guesses as a last resort; at most
  6 pages and an early stop once a site has enough contacts.
- **Analysis** (`heuristics`, `analysis`, `extractor`): addresses from `mailto:`, Cloudflare-protected links and
  visible text (de-obfuscated); people from JSON-LD, microdata, repeated team cards and heading/title pairs. The model
  is asked only for team pages those cannot read, and only names that literally appear on the page are kept.
- **LLM queue** (`llmqueue.PeopleBatcher`): one consumer, a bounded queue, several page snippets per call (about
  3,000 tokens), never two generations at once. The crawl never waits for it.
- **Validator** checks syntax, drops disposable-mailbox domains, and requires an MX record (positive lookups cached
  in memory, "no mail server" answers persisted for 30 days).
- **Scoring** (`scoring`): 0-100 from the label, name and role match, page type, MX and freshness; every point has a
  reason that is stored with the lead.
- **Leads** (`leads.ingest`): suppressed addresses skipped; the same email or the same person (name + registrable
  domain) is never stored twice (a published address replaces a guess); every source page goes to `lead_sources`
  and every decision to `trace_events` under the lead's `trace_id`, mirrored to `logs/pipeline.jsonl`.

If a run finishes short of its target (default 30 leads) it **widens**: fresh search wording, more list pages, only
sites not yet seen, for up to 4 rounds, about 12 minutes, or the site limit. Each site keeps at most 5 leads.
A run can be **cancelled** (takes effect within about two seconds, keeps what was found) and **resumed**: per-URL
state is in `crawl_urls`, so finished sites are skipped and fetched pages come from the cache.

The UI follows a run through four SSE event types: `run` (stage, counters and the engine statistics), `run_domain`,
`run_log`, and `lead`. State is also in the database (`search_runs`, `run_domains`, `run_log`), so a reload rebuilds
the screen.

## Sending, tracking and follow-ups

All outside-world access goes through ports (`ports.py`: `Mailer`, `ImapClient`, `LlmClient`, `PageCache`, `Clock`)
handed out by `services.get_services()`; tests swap in `fakes.py`, so no test ever touches SMTP, IMAP, Ollama or DNS.

- **Compose** (`composer`, `personaliser`, `drafts`): Jinja2 template plus one LLM `custom_opener`, validated
  (35 words, grounded in the stored snippet, no invented numbers, names or URLs); failures fall back to a neutral
  opener and `needs_review`. Drafts never contain the footer; a DB trigger withdraws approval on any edit.
- **Send** (`outbox`, `sender`, `mailer`, `campaigns`): `outbox` owns the message state machine
  (`queued -> sending -> sent | failed | needs_confirmation | cancelled`) with a unique index per (campaign, lead,
  kind). `claim` is one SQL statement re-checking approval, suppression, pause-all, the daily cap and 100/day
  ceiling, per-company limits and spacing. `sender` adds jitter (`campaigns.next_send_at`), the send-time footer
  (`footer.ensure_footer`), bounce auto-pause, SMTP-login stop and graceful shutdown. `tz` applies the window in the
  recipient's zone when it can be inferred. Mid-send crashes become `needs_confirmation`, never auto-resent.
- **Track** (`tracker`, `inbox_poller`, `threads`, `metrics`): read-only IMAP (`BODY.PEEK`, INBOX), replies matched by
  `In-Reply-To`/`References`, DSN bounce parsing, auto-reply and opt-out detection, idempotent on the incoming
  Message-ID, stronger lead statuses never overwritten by weaker ones.
- **Follow-ups** (`followups`): due times are cumulative from the first email; each follow-up is an ordinary
  `messages` row, held until its threading headers are set, so it passes through the same outbox checks.
- Events `draft`, `campaign`, `message`, `inbox`, `sending` (`event_types.py`) are invalidation hints; the UI
  refetches by query-key prefix (`desktop/src/lib/api/keys.ts`).

## Supervision

Electron picks the port and token, spawns uvicorn and the worker from `.venv`, and polls `/health`
behind a splash screen. A crashed process is restarted (up to 3 times a minute). After that the app
shows an error screen with Try again, View logs, and Quit. Both processes exit if Electron dies
(they watch their parent PID), and quitting stops them with SIGTERM, then SIGKILL after 3 s.

### Packaged layout

`scripts/build-mac.sh` freezes the backend with PyInstaller into one `reachout-backend` executable
(`scripts/backend_entry.py`: roles `api`, `worker`, `selfcheck`; SQL migrations are bundled as data)
and electron-builder ships it as an extraResource, so the app contains
`Contents/Resources/{app.asar,backend/reachout-backend/}`. When `app.isPackaged`, the supervisor
spawns that binary instead of `.venv` python and ignores `ELECTRON_RENDERER_URL`; data, logs and the
Keychain service are unchanged. The build is unsigned arm64 only (`dist/` and `.build/` are ignored).

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
