# Reachout

A local, free, Mac-only outreach tool. Describe who you want to contact; Reachout finds public
contact emails, drafts personalised emails with a small local LLM, and sends them in rate-limited
batches **only after you approve them**. See `app-spec.md` (behaviour) and `ui-migration-spec.md`
(desktop UI) for the full spec, and `ARCHITECTURE.md` for how it fits together.

**Status:** all feature milestones are built: Discover and Leads, Compose (templates, local-LLM openers, draft
review), Campaigns (sending), Inbox and Dashboard (replies, bounces, opt-outs), and follow-ups. Packaging
(`.dmg`) is the remaining step. Nothing has been sent to a real recipient by the automated tests; try a
**Send test to myself** before your first real campaign.
The previous Streamlit version is preserved at the git tag `streamlit-m1`.

## Requirements

- macOS on Apple Silicon (built for an 8 GB M3) and [Homebrew](https://brew.sh)
- `scripts/setup.sh` installs the rest: `uv` (which fetches Python 3.11), Node 24 LTS via
  [nvm](https://github.com/nvm-sh/nvm) if you have it, and Ollama
- No paid services and no API keys. The only external account is your own Gmail.

## Setup and run

```bash
scripts/setup.sh   # Python 3.11 venv, npm dependencies, Electron, data folder, Ollama. Safe to re-run.
scripts/run.sh     # builds and opens the app (no browser tab, no terminal interaction after launch)
scripts/dev.sh     # development: hot-reloading UI, API restarts on backend changes
```

On first launch the **setup wizard** checks Ollama, downloads the model if needed (about 2 GB for
`qwen2.5:3b-instruct`, with live progress), tests your Gmail login, and asks for your name, the
opt-out footer, and acknowledgement of the legal notice.

### Gmail App Password

1. Use a **separate Gmail account** for cold outreach, not your primary one.
2. Turn on 2-Step Verification, then Google Account > Security > App passwords.
3. In Gmail settings enable IMAP (Forwarding and POP/IMAP).
4. Paste the 16-character password into the wizard or Settings > Account. It goes to the **macOS
   Keychain** (service `Reachout`) and never to a file or the database. If macOS asks whether
   `python` may use the Keychain item, choose *Always Allow* so the background worker can read it.

## Finding leads

1. **Discover**: describe who you want to reach (or pick an example), choose a mode or leave it on Auto,
   and press *Make a plan*. The local model turns the request into roles, industries, regions and search
   queries in a few seconds. Edit any of it, then *Run search*.
2. The worker searches the web, then crawls many sites at once (30 by default) but each site politely: one
   request at a time, 1-2 s apart (or the site's `Crawl-delay`), robots.txt respected, at most 6 pages per
   site, and it stops early once a site has enough contacts. Pages are cached for 14 days, so a repeat search
   fetches almost nothing. It pulls out emails and people (page structure first, the local model only when
   that is not enough), checks the domain has a mail server, scores each lead, and saves it. You watch it
   happen: a stepper, a streaming log, a table of sites and a "How the crawl went" panel. A cancelled or failed
   search can be **resumed**: finished sites and fetched pages are kept.
3. **Leads**: a fast table of everything found. Search, filter, sort (Shift-click for a second sort key),
   edit cells by double-clicking, open the side drawer for the source page and the exact text the lead came
   from, select rows for bulk actions, and import or export CSV. Every search also writes
   `~/Reachout/exports/leads-run-<n>.csv`.

Every lead has a **score from 0 to 100** and the reasons behind it (open the drawer): the label below is
worth the most, then a name and role that match the address and your request, the kind of page it came
from, a working mail server, and how fresh the page is. The drawer also lists every page the address was
seen on and a step-by-step "Why was this found?" trace. Sort or filter the table by score.

Every lead is labelled so you know how far to trust the address:

| Label | Meaning |
| --- | --- |
| **Personal** | A person's address, published on their own site, next to a name that matches it |
| **Generic** | A role address such as `info@`, `careers@`, `hello@`, or a personal-looking address with no matching name on the page |
| **Guessed** | Built from a listed person's name and the domain's address pattern. Only the domain's mail server is checked. Never sent to unless you allow it in Settings |

The same address, or the same person (name + company domain), is never stored twice: a published address
replaces a guessed one and the extra source pages are kept. Throwaway-mailbox domains are dropped.

A search aims for about 30 leads and keeps going with new wording if it is short. Measured results for the
three example requests are in [`docs/m2-yields.md`](docs/m2-yields.md): 32, 36 and 38 leads in 5 to 9 minutes,
but very few are `Personal`, and relevance depends heavily on what the search engine returns.

What the pipeline will not do: crawl LinkedIn, Facebook, Instagram, X, Reddit, job boards, news and
directory sites, GitHub or other code hosts, universities or government sites; log in anywhere; or
bypass robots.txt or a CAPTCHA. It only keeps addresses on the site's own domain.

**Try it from a terminal** (real search, real crawl, real model, in a throwaway data folder):

```bash
REACHOUT_HOME=/tmp/reachout-try PYTHONPATH=. .venv/bin/python -m backend.cli discover \
  "Pre-seed investors who back AI developer tools in India/SEA" --max-domains 40
```

## Crawl engine (milestone 2b)

Async HTTP (`httpx`, pooled connections) with a waterfall: **page cache** (SQLite, conditional requests with
ETag / Last-Modified, robots and "not found" answers cached too) then **HTTP**, then an optional headless
**browser** for JavaScript-only pages. The browser tier needs Playwright and is off by default:

```bash
.venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium   # then enable it in Settings > Crawling
```

Without it, JavaScript-only pages are simply skipped and counted. Settings > Crawling controls concurrency,
the cache period and the crawler contact (a URL or email shown to site owners in the User-Agent; blank by
default, with a warning, and never filled in from your Gmail address).

Measured numbers (`scripts/bench_crawl.py`, no model): see [`docs/m2b-benchmarks.md`](docs/m2b-benchmarks.md).

## How a campaign flows

Discover finds leads -> Compose writes one draft per lead (your template plus one short LLM-written opener,
checked against the page it came from) -> you review and approve -> Campaigns: readiness checklist, **Send test to
myself**, **Dry run**, **Start** -> the worker sends at most 25 a day (never above 100), 60 to 180 s apart, inside the
send window, with the opt-out footer -> Reachout polls your Gmail every 10 minutes (read-only) and marks replies,
bounces, out-of-office and opt-outs -> follow-ups (up to 2, day 4 and 9 by default) go to people who stayed silent.
Opens are not tracked. The campaign auto-pauses when more than 5% bounce after at least 20 sends.

## Send safety

`backend/core/outbox.py` is the state machine behind the sender: one message per person per campaign (enforced by the
database), an atomic claim that re-checks approval, suppression, the daily cap, per-company limits and the send window,
and crash recovery: an email caught between `sending` and `sent` becomes **needs confirmation** and is never resent
automatically. Editing an approved draft withdraws its approval.

## Where things live

| What | Where |
| --- | --- |
| Database, exports | `~/Reachout/` (override with `REACHOUT_HOME`) |
| Logs | `~/Reachout/logs/` (`desktop.log`, `api.log`, `worker.log`, `pipeline.jsonl`: one JSON line per lead decision with a correlation id); Help > Show Logs |
| App Password | macOS Keychain, service `Reachout` |
| Non-secret settings | `settings` table in the SQLite DB |

## Safety rules (enforced in the backend, not just the UI)

- Nothing sends without your approval, and editing a draft after approving it withdraws the approval.
- Every email carries the opt-out footer (it cannot be switched off for service and investor campaigns).
- A message is never sent twice, and one found mid-send after a crash waits for your confirmation.
- The suppression list is always enforced. Bounce and opt-out entries can never be removed from the
  UI; entries you add by hand can, after typing the address to confirm.
- The daily send cap is clamped to a hard ceiling of 100 whatever the setting says.
- The API only listens on `127.0.0.1`, requires a per-launch token on every request, and rejects
  foreign origins and hosts.

## Tests

```bash
scripts/test-fast.sh                                # inner loop: only what you changed
scripts/test-all.sh                                 # full gate: backend, frontend, types, lint, safety coverage (add --e2e for Playwright)
.venv/bin/python -m pytest -q -n 2                  # backend only
cd desktop && npm test                              # frontend unit/component tests (Vitest)
cd desktop && npm run test:e2e                      # Electron tests (Playwright): shell, lifecycle, Discover and Leads
PYTHONPATH=. .venv/bin/python scripts/bench_crawl.py   # crawl benchmark on a fixed list of 50 domains (real network, no model)
cd desktop && REACHOUT_LIVE=1 npm run test:e2e      # also one real search through the app (needs internet + Ollama, ~2 min)
cd desktop && npm run typecheck && npm run lint     # strict TypeScript, no `any`
cd desktop && npm run gen:api -- --check            # generated API types are up to date
```

Tests use temporary data folders and an in-memory keyring; they never touch your real `~/Reachout/`
or Keychain. After changing a backend schema or endpoint, run `cd desktop && npm run gen:api`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Splash shows an error | Click **View logs**, or open `~/Reachout/logs/api.log` |
| Wizard says Ollama is not running | `brew services start ollama` (or `ollama serve`) |
| "Node 24 LTS is required" | `nvm install` (reads `.nvmrc`), or `brew install node@24` |
| Gmail test fails | Use an App Password, not your normal password, and enable IMAP |
| App will not start a second time | It is single-instance: quit the running copy first |
| Stale or broken Python environment | `rm -rf .venv && scripts/setup.sh` |
