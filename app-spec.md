# app-spec.md — Reachout (local, free, Mac-only outreach tool)

## 1. Purpose

A fully local macOS app where I describe in plain English who I want to contact, and it:

1. Finds public contact emails for matching people/organisations on the web and saves them to SQLite and a CSV.
2. Drafts a personalised email per lead using a small local LLM.
3. Lets me review the drafts, then send them in rate-limited batches with one click.
4. Tracks sent, bounced, replied and follow-up status automatically.

It must cost nothing to run: no paid APIs, no API keys required, no cloud services. The only external accounts are my own Gmail (SMTP/IMAP via App Password) and public websites/search engines.

### My three use cases (these shape every default)

| Mode         | Example request                                               | Typical targets                                                                                |
| ------------ | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `internship` | "Software/AI internships at seed-stage startups in Bangalore" | Founders, CTOs, engineering managers, recruiters, `careers@`/`jobs@` addresses                 |
| `investor`   | "Pre-seed investors who back AI developer tools in India/SEA" | Partners/associates at VC firms and angel syndicates, published `pitch@`/`founders@` addresses |
| `service`    | "Founders of D2C brands who need a website redesign"          | Founders/owners/marketing heads of small and mid-size companies                                |

The mode picks the search strategy, which pages to crawl, which roles to prefer, and which email template to start from.

## 2. Hard constraints

- **Hardware:** MacBook Pro M3, **8 GB unified memory**. Everything (LLM + browser + app) must fit comfortably. Never run two LLMs at once.
- **LLM:** Ollama, default model `qwen2.5:3b-instruct` (fallbacks: `llama3.2:3b`, `qwen3:4b`). Model name is configurable in Settings. Set `num_ctx` to 4096, `keep_alive` to 5m, and truncate page text fed to the model to about 3,000 tokens.
- **No paid services, no required API keys.** Optional free keys may be supported later but are off by default.
- **Local only:** data in `~/Reachout/` (SQLite DB, CSV exports, logs). Nothing is uploaded anywhere except outgoing emails and web requests.
- **Python 3.11**, virtualenv, one-command setup (`./setup.sh`) and one-command run (`./run.sh`).
- **Secrets:** Gmail App Password stored in macOS Keychain via `keyring`, never in plaintext files or the DB.

## 3. Tech stack

| Concern                              | Choice                                                                                                            |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| UI                                   | Streamlit (multi-page app)                                                                                        |
| DB                                   | SQLite via SQLModel or SQLAlchemy (single file, migrations via simple versioned SQL)                              |
| LLM                                  | `ollama` Python client, JSON-schema structured outputs                                                            |
| Web search                           | `ddgs` (DuckDuckGo) as primary; optional local SearXNG URL in Settings                                            |
| Fetching                             | `httpx.AsyncClient` (async, pooled) with an honest UA + `trafilatura` for text; Playwright (Chromium) as a lazy third tier for JS-only pages. Engine details in section 13 |
| HTML parsing                         | `selectolax` or `beautifulsoup4`                                                                                  |
| Local business source (service mode) | OpenStreetMap Overpass API (free, no key) — many businesses carry `website` / `contact:email` tags                |
| Email validation                     | `email-validator` (syntax) + `dnspython` (MX lookup)                                                              |
| Sending                              | `smtplib` over `smtp.gmail.com:465`                                                                               |
| Reply/bounce tracking                | `imaplib` over `imap.gmail.com:993`                                                                               |
| Background jobs                      | A separate worker process using APScheduler (so Streamlit reruns never interrupt jobs); job state lives in SQLite |
| Tests                                | `pytest`                                                                                                          |

## 4. Core flows

### 4.1 Lead discovery ("Find Leads" page)

1. I type a request and pick a mode (auto-detected by the LLM, but editable).
2. **Planner (LLM):** convert the request to a JSON object validated by Pydantic:
   ```json
   {
     "mode": "internship|investor|service",
     "target_roles": ["founder", "CTO"],
     "industries": ["AI", "developer tools"],
     "regions": ["India"],
     "keywords": ["seed", "hiring interns"],
     "search_queries": ["... up to 12 varied queries ..."]
   }
   ```
   Show the plan and let me edit it before anything runs.
3. **Discovery:** run the queries through `ddgs`, collect result URLs, dedupe by domain, skip a blocklist (LinkedIn, Facebook, Instagram, X, Reddit, Glassdoor, Indeed, Crunchbase, news aggregators, etc.). In `service` mode, optionally query Overpass for businesses by category and region.
4. **Crawl:** for each candidate domain fetch the page plus likely subpages (`/contact`, `/about`, `/team`, `/people`, `/careers`, `/jobs`, `/pitch`, `/investors`, `/portfolio`). Respect `robots.txt`, use 1–2 s delay per domain, cap pages per domain (default 6), 15 s timeout, and show live progress.
5. **Extract:**
   - Emails: regex on visible text and `mailto:` links, with de-obfuscation for `name [at] domain [dot] com`. Reject image-like and placeholder addresses and `noreply`/`no-reply`/`donotreply`.
   - People and roles: LLM pulls `{name, role, company}` from the page text (structured output, max one call per page; skip pages without an email or a team-like structure to save time).
   - Store the **source URL** and a short **context snippet** for each lead (used later for personalisation and for showing me where it came from).
6. **Email confidence label** for each lead (store in `email_confidence`):
   - `published_personal`: personal address found on a page, name matches.
   - `published_generic`: `info@`, `contact@`, `careers@`, `hello@`, `pitch@`, etc.
   - `guessed`: generated from patterns (`first@`, `first.last@`, `flast@`) for a known person at a domain. Only MX-checked, never SMTP-verified. **Excluded from sending by default**; a toggle in Settings allows it with a warning.
7. **Validate:** syntax + MX check; drop failures. Dedupe on lowercase email across all runs. Skip anyone on the suppression list.
8. **Output:** save to SQLite, show an editable table (filter, select, delete rows), and a **Download CSV** button. CSV columns:
   `name, email, role, company, domain, source_url, email_confidence, mode, notes`.
9. Also support **Import CSV** (same columns) so I can add leads from elsewhere.

### 4.2 Templates and personalisation ("Compose" page)

- I keep one or more campaign templates (subject + body) with merge fields: `{{first_name}}`, `{{company}}`, `{{role}}`, `{{my_name}}`, `{{custom_opener}}`, plus any free-form profile fields I define (e.g. my résumé summary, my service pitch, my traction).
- Ship three starter templates (internship, investor, service). Each is short (under 120 words), plain text, with a clear single ask.
- **Hybrid personalisation (important for a 3B model):** the LLM writes only a 1–2 sentence `custom_opener` based strictly on the stored context snippet. The rest is my template, rendered with Jinja2. Do not ask the small model to write whole emails.
- Guardrails on the generated opener: max 35 words; must reference something present in the context snippet; no invented facts, numbers or URLs; banned phrases ("I hope this finds you well", "I came across your", "game-changing", "synergy", etc.). If a check fails, retry once, then fall back to a neutral opener, and mark the draft `needs_review`.
- Every draft is stored in the DB. The **Review** table shows lead, subject, body, flags; I can edit any draft inline, approve or reject, and bulk-approve. **Nothing sends without approval.**
- Attachments: optional single PDF (my résumé) per campaign, attached to every email. Warn if the file is over 2 MB.
- Every email gets a footer line from Settings (my real name, and "If you'd rather not hear from me, just reply 'stop' and I won't email again."). The footer cannot be disabled in `service` and `investor` modes.

### 4.3 Sending ("Send" page)

- Campaign = a set of approved drafts + template + settings.
- Buttons: **Send test to myself**, **Dry run** (log only), **Start sending**, **Pause**, **Resume**.
- **Safety defaults (all editable in Settings, but with warnings if raised):**
  - Daily cap: 25. Warm-up ramp suggestion: 10, 15, 20, 25, 30 over the first five sending days.
  - Random delay between sends: 60–180 s.
  - Send window: 09:00–18:00 local time, Mon–Sat.
  - Hard ceiling of 100/day regardless of setting.
  - Auto-pause the campaign if bounces exceed 5% after at least 20 sends, and show why.
  - Never email an address twice in the same campaign, anyone on the suppression list, or anyone who replied.
- Sending runs in the worker process, survives closing the browser tab, and resumes after restart (state is in SQLite). If the Mac sleeps, it simply continues when awake. Use `caffeinate` only while a send run is active and tell me.
- Each email: unique `Message-ID` saved to DB, proper `From` display name, plain text only (no tracking pixels, no link rewriting).
- Show a clear error and stop the run if SMTP login fails (don't retry-loop).

### 4.4 Tracking ("Dashboard" page)

- Worker polls Gmail IMAP every 10 minutes (and on a "Check now" button):
  - **Replied:** an incoming message whose `In-Reply-To` or `References` matches a stored `Message-ID`; fall back to matching sender address + subject. Store the reply snippet and timestamp. Ignore out-of-office/auto-replies (detect via `Auto-Submitted`, `X-Autoreply`, `Precedence: bulk`, and subject patterns) and record them as `auto_reply`, not `replied`.
  - **Bounced:** parse DSN/"Mail Delivery Subsystem"/"Undelivered Mail" messages, extract the failed recipient and status code; hard bounces (5.x.x) mark the lead `bounced` and add it to suppression.
  - **Unsubscribe:** replies whose first lines contain "stop", "unsubscribe", or "remove me" are marked `opted_out` and added to the suppression list automatically (and flagged for me to double-check).
- **Follow-ups:** per campaign, optionally up to 2 follow-ups at N days (default 4 and 9) if no reply/bounce/opt-out. Follow-ups are sent in the same thread (`In-Reply-To`, `References`, `Re:` subject), use a short template, and go through the same caps and approval (default: follow-ups for approved campaigns are auto-queued but I can toggle "require approval").
- **Metrics (per campaign and overall):** leads found, drafts approved, sent, delivered (= sent minus bounced), bounced, replied, reply rate, opted out, follow-ups sent, awaiting follow-up. Funnel chart, a daily-sends chart, and a lead table with status filter and a link to open the Gmail thread.
- **Opens are explicitly not tracked** (unreliable and needs a public server). Show a note in the UI saying so.
- Let me manually set a lead's status (e.g. "interview scheduled", "not interested") and add notes, and export any filtered view to CSV.

## 5. Pages

1. **Find Leads:** request box, mode, plan editor, run button with live log, results table, CSV export/import.
2. **Compose:** templates, my profile fields, generate/preview drafts, review and approve.
3. **Send:** campaign setup, test send, start/pause, live progress.
4. **Dashboard:** metrics, funnel, per-lead statuses, replies, bounces.
5. **Settings:** Gmail address + App Password (Keychain), display name, footer, Ollama model, send limits and window, suppression list viewer, crawl limits, search source.
6. **First-run wizard:** checks that Ollama is installed and running, pulls the model if missing (show progress), tests Gmail SMTP and IMAP login, creates the data folder.

## 6. Data model (SQLite)

- `leads(id, name, email UNIQUE, role, company, domain, source_url, context_snippet, email_confidence, mode, status, notes, created_at)`
  - `status`: `new | drafted | approved | queued | sent | replied | bounced | opted_out | auto_reply | closed`
- `search_runs(id, request_text, plan_json, started_at, finished_at, pages_fetched, leads_found)`
- `templates(id, mode, name, subject, body, followup_1_body, followup_2_body)`
- `campaigns(id, name, template_id, attachment_path, daily_cap, window_start, window_end, followups_enabled, followup_days_json, state, created_at)`
- `drafts(id, campaign_id, lead_id, subject, body, flags_json, approved, created_at)`
- `messages(id, lead_id, campaign_id, kind(initial|followup_1|followup_2), message_id, subject, sent_at, smtp_result)`
- `events(id, lead_id, message_ref, type(reply|bounce|auto_reply|opt_out), snippet, occurred_at)`
- `suppression(email PRIMARY KEY, reason, added_at)`
- `settings(key, value)` for non-secret settings only

## 7. Legal, ethical and safety rules to build in

- Respect `robots.txt`; do not scrape LinkedIn, Facebook, Instagram or X; do not log in to any site; no CAPTCHA bypassing.
- **Do not harvest emails from GitHub commits or profiles** (GitHub's terms restrict using that data for solicitation).
- Collect only publicly published business contact details. Store the source URL for every lead so I can justify why I have it.
- Honest emails only: real sender name, no fake "Re:" subjects on first emails, no deceptive subjects.
- Suppression list is checked at every send, including follow-ups, and cannot be bypassed from the UI.
- Show a short notice in the app about CAN-SPAM, GDPR/PECR (EU/UK recipients) and India's DPDP Act, and tell me to avoid emailing individuals in the EU/UK in `service` mode.
- Recommend (in the wizard) using a separate Gmail account for cold outreach, not my primary account.

## 8. Project structure

```
reachout/
  app/
    main.py                 # Streamlit entry
    pages/1_Find_Leads.py ... 5_Settings.py
    core/
      config.py db.py models.py secrets.py
      llm.py                # Ollama wrapper, JSON-schema calls, retries
      planner.py            # request -> plan
      discovery.py          # ddgs + overpass
      crawler.py            # fetch, robots, politeness
      extractor.py          # emails, people, roles, context
      validator.py          # syntax, MX, dedupe, confidence labels
      personaliser.py       # opener generation + guardrails
      mailer.py             # SMTP send, threading, attachments
      tracker.py            # IMAP replies/bounces/opt-outs
      scheduler.py          # worker, send queue, follow-ups
      exports.py
    worker.py               # background process entry
  tests/
  setup.sh  run.sh  requirements.txt  README.md
```

## 9. Build milestones (build and verify each before the next)

1. **Skeleton:** repo, setup/run scripts, SQLite schema, Settings page, Keychain secret storage, first-run wizard (Ollama + Gmail checks).
2. **Lead pipeline:** planner, discovery, crawler, extractor, validator, results table, CSV export/import. Test with the three example requests in section 1 and report how many leads each yields and what share are `published_personal`.
   - **2b. Production-grade crawling and outreach engine (section 13):** async tiered crawler with a page cache, heuristics before the LLM, a pipelined and resumable run, lead scoring and provenance, and the send-safety core (no SMTP yet). Done before milestone 3.
3. **Compose and review:** templates, hybrid opener generation, guardrails, draft review/approval UI.
4. **Sending:** worker process, caps, delays, windows, test send, dry run, pause/resume, auto-pause on bounces.
5. **Tracking:** IMAP poller, reply/bounce/auto-reply/opt-out detection, suppression list, dashboard.
6. **Follow-ups:** scheduling, threading, UI.
7. **Polish:** error handling, logs page, README with screenshots, `pytest` suite.

## 10. Acceptance criteria

- `./setup.sh && ./run.sh` works on a clean M3 Mac with 8 GB RAM and Homebrew installed; memory use stays under ~6 GB total while the model is loaded.
- Typing "AI/ML internship openings at startups in Bangalore" produces a CSV of at least 30 MX-valid leads with source URLs within about 15 minutes (report honestly if yields are lower and why).
- Drafts never contain facts absent from the lead's context snippet (verified by the guardrail tests).
- A test campaign to 3 of my own addresses sends, shows correct threading, detects my reply as `replied`, and detects a deliberate bad address as `bounced`.
- Closing the browser tab mid-campaign does not stop sending; restarting the app resumes it.
- No email is ever sent to a suppressed address (unit-tested).
- Milestone 2b (section 13.D): the crawl, cache, memory and LLM-call targets are measured with `scripts/bench_crawl.py` and reported.
- Unit tests cover: email extraction and de-obfuscation, MX validation, confidence labelling, guardrails, send cap/window logic, IMAP reply/bounce matching (with fixture `.eml` files), suppression enforcement.

## 11. Out of scope for v1

Open/click tracking, LinkedIn automation, multi-user support, cloud hosting, HTML-designed emails, A/B testing, CRM integrations, paid data providers.

## 12. Working agreement for Claude Code

- Read this whole file first, then propose the plan for milestone 1 and wait for my "go".
- Build one milestone at a time; run the tests and a manual check, then summarise what works, what doesn't, and any deviation from this spec before starting the next.
- Prefer small, well-maintained libraries; no heavy infrastructure (no Docker, Redis, Postgres, Kafka).
- Ask me before adding any dependency that needs an API key or costs money.
- Keep `README.md` current with setup, usage and troubleshooting.

## 13. Production-grade crawling and outreach engine

This section tightens sections 4.1 and 4.3 and wins where it conflicts with them. Behaviour, data-model, safety and legal rules elsewhere in this file are unchanged. Everything here is local and free: no paid services, no API keys, no Docker/Redis/Postgres.

### 13.A Crawler

1. **Async, many domains at once.** `httpx.AsyncClient` with connection pooling (HTTP/2 only if a benchmark shows it helps) and an `asyncio` worker pool. A **global concurrency limit** (default 30, setting 1-64) and a **per-domain limit of 1 concurrent request** with a 1-2 s delay, raised to the site's robots `Crawl-delay` when that is larger (honoured up to 10 s; a longer value means the domain is skipped for the run and the reason recorded). Parallelism comes from many domains, never from hammering one.
2. **Tiered fetching ("waterfall").** (a) page-cache hit, (b) plain HTTP fetch, (c) Playwright only when the HTML has no usable content (a JS-only shell). Playwright is optional and lazy: one reused browser, at most 2 concurrent pages, images/fonts/media blocked, and the tier is skipped cleanly if the browser is not installed. The tier that served each URL is logged and counted.
3. **SQLite page cache.** Fetched HTML/text keyed by URL hash with ETag, Last-Modified, `fetched_at` and a TTL (default 14 days). Conditional requests for stale entries; fresh pages are never refetched. `robots.txt` is cached per domain, and **negative results** (dead domains, no-MX domains, blocked domains, tripped circuit breakers) are cached with an expiry so repeat runs are fast.
4. **Smart crawl planning.** Per domain: the homepage, then links to contact / about / team / people / careers / jobs / investors / portfolio / pitch pages discovered from the homepage and from `sitemap.xml` (including `Sitemap:` lines in robots.txt). At most 6 pages per domain (setting). Blind path guessing is a last resort (at most 3 guesses, only when nothing was discovered). The domain stops early once enough contacts are found.
5. **Heuristics before the LLM.** Plain code first: emails, `mailto:` links, JSON-LD (`Person`, `Organization`), schema.org microdata, and obvious team-card HTML patterns. The local LLM is called **only** for pages where heuristics found an email or a team-like block but could not determine name or role. Short snippets are **batched** into one call, input capped at about 3,000 tokens, and LLM calls never run concurrently (a single queue). The stages (fetch, extract, LLM, validate, store) are connected by **bounded queues** so memory stays flat and the HTTP crawl keeps running while the LLM works.
6. **Resilience.** Connect timeout 5 s, total deadline 15 s, 2 MB response cap, content-type checks. Retries (max 2) with exponential backoff and jitter on 429, 5xx and network errors, honouring `Retry-After`. A per-domain circuit breaker skips a domain after repeated failures. Redirects, odd encodings and malformed HTML never crash a job.
7. **Resumable and cancellable.** Per-URL state is persisted in SQLite so an interrupted run continues where it stopped. Cancel takes effect within about 2 seconds, keeping everything found so far.
8. **Observability.** Per-run stats saved to the DB, streamed live over SSE to the Discover screen and shown in the run summary: domains, pages by tier, cache hit rate, pages/sec, failures by reason, LLM calls and time, and time per stage.
9. **Ethics and safety (unchanged, non-negotiable).** Respect robots.txt; an honest User-Agent that identifies Reachout and includes the contact URL or email from Settings (blank by default, with a warning; it is never defaulted to the user's own address); no LinkedIn, Facebook, Instagram or X; no logins; no CAPTCHA solving; no stealth or fingerprint evasion; no proxy rotation. If a site blocks us (403, 429, a CAPTCHA page), record it and move on.

### 13.B Lead quality

1. **Score 0-100** per lead from: email confidence label (`published_personal` > `published_generic` > `guessed`), name/email match, role match with the request, source page type, MX validity and freshness. The score and its reasons are stored; the Leads table can sort and filter by score.
2. **Dedupe across runs** by email and by (name, domain). Sources are **merged**, not dropped. Provenance (source URL, page tier, `fetched_at`, run) is kept per lead.
3. **Domain hygiene.** Disposable domains, the role-address blocklist (`noreply`, `abuse`, `privacy`, ...) and domains with no MX record are skipped.

### 13.C Outreach engine (sending side)

1. **Idempotent, crash-safe sending.** A message is marked `sending` in SQLite **before** the SMTP call and `sent` after. After a restart anything left in `sending` is **never resent automatically**; it becomes `needs_confirmation` and the user decides. A person must never receive the same email twice (enforced by a unique key per campaign, lead and kind, not only by checks).
2. **Per-recipient-domain throttling.** At most 2 emails per company domain per day, and never to two people at one company within the same hour (both configurable).
3. **Time zones.** Where a recipient time zone can be inferred (the plan's region or the country TLD), the send window is applied in that zone; otherwise in the sender's local time as in section 4.3.
4. **Every existing safety rule stays**: daily caps and warm-up, delay jitter, send window, the 100/day ceiling, bounce auto-pause, suppression check at send time, approval required, opt-out footer.
5. **Traceability.** Structured logging with a correlation id per lead and message, so any crawl or send decision can be traced from the UI ("why was this lead found / emailed?").
6. **Scope note.** Milestone 2b builds the state machine, guards and tests without SMTP; milestone 4 connects `smtplib` to it.

### 13.D Verification and targets

- `scripts/bench_crawl.py` runs a fixed domain list reproducibly. Unit tests use recorded HTML fixtures (extractor, robots, cache, retry and circuit breaker, idempotent send including a simulated crash between `sending` and `sent`).
- Targets on a normal home connection: crawl **200 domains (up to 6 pages each) in under 10 minutes** excluding LLM time; backend process peak RSS under about 1.5 GB; far fewer LLM calls per 100 leads than the one-call-per-page baseline (about 320). Report honestly if a number falls short, and why.
