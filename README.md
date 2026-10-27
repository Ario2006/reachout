# Reachout

A local, free, Mac-only outreach tool. Describe who you want to contact; Reachout finds public
contact emails, drafts personalised emails with a small local LLM, and sends them in rate-limited
batches **only after you approve them**. See `app-spec.md` (behaviour) and `ui-migration-spec.md`
(desktop UI) for the full spec, and `ARCHITECTURE.md` for how it fits together.

**Status:** Milestone 2 of the revised plan. Done: the desktop shell, first-run wizard, Settings, and the
lead pipeline (**Discover** and **Leads**). Compose, Campaigns, Inbox and Dashboard appear in the
sidebar as "Soon".
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
2. The worker searches the web, crawls each site politely (robots.txt respected, 1-2 s between requests
   to a site, 6 pages per site by default), pulls out emails and people, checks the domain has a mail
   server, and saves the result. You watch it happen: a stepper, a streaming log, and a table of sites.
3. **Leads**: a fast table of everything found. Search, filter, sort (Shift-click for a second sort key),
   edit cells by double-clicking, open the side drawer for the source page and the exact text the lead came
   from, select rows for bulk actions, and import or export CSV. Every search also writes
   `~/Reachout/exports/leads-run-<n>.csv`.

Every lead is labelled so you know how far to trust the address:

| Label | Meaning |
| --- | --- |
| **Personal** | A person's address, published on their own site, next to a name that matches it |
| **Generic** | A role address such as `info@`, `careers@`, `hello@`, or a personal-looking address with no matching name on the page |
| **Guessed** | Built from a listed person's name and the domain's address pattern. Only the domain's mail server is checked. Never sent to unless you allow it in Settings |

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

## Where things live

| What | Where |
| --- | --- |
| Database, exports | `~/Reachout/` (override with `REACHOUT_HOME`) |
| Logs | `~/Reachout/logs/` (`desktop.log`, `api.log`, `worker.log`); Help > Show Logs |
| App Password | macOS Keychain, service `Reachout` |
| Non-secret settings | `settings` table in the SQLite DB |

## Safety rules (enforced in the backend, not just the UI)

- Nothing sends without your approval (built in milestones 3 and 4).
- The suppression list is always enforced. Bounce and opt-out entries can never be removed from the
  UI; entries you add by hand can, after typing the address to confirm.
- The daily send cap is clamped to a hard ceiling of 100 whatever the setting says.
- The API only listens on `127.0.0.1`, requires a per-launch token on every request, and rejects
  foreign origins and hosts.

## Tests

```bash
.venv/bin/python -m pytest -q                       # backend
cd desktop && npm test                              # frontend unit/component tests (Vitest)
cd desktop && npm run test:e2e                      # Electron tests (Playwright): shell, lifecycle, Discover and Leads
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
