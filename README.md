# Reachout

A local, free, Mac-only outreach tool. Describe who you want to contact; Reachout finds public
contact emails, drafts personalised emails with a small local LLM, and sends them in rate-limited
batches **only after you approve them**. See `app-spec.md` (behaviour) and `ui-migration-spec.md`
(desktop UI) for the full spec, and `ARCHITECTURE.md` for how it fits together.

**Status:** Milestone 1b of the revised plan. The desktop shell, first-run wizard and Settings are
done. Discover, Leads, Compose, Campaigns, Inbox and Dashboard appear in the sidebar as "Soon".
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
.venv/bin/python -m pytest -q                       # backend (99 tests)
cd desktop && npm test                              # frontend unit/component tests (Vitest)
cd desktop && npm run test:e2e                      # Electron smoke + lifecycle tests (Playwright)
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
