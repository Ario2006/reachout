# Reachout

A local, free, Mac-only outreach tool. Describe who you want to contact; Reachout finds public
contact emails, drafts personalised emails with a small local LLM, and sends them in
rate-limited batches **only after you approve them**. See `app-spec.md` for the full spec.

**Status:** Milestone 1 of 7 (skeleton: Settings, Keychain, first-run wizard). The Find Leads,
Compose, Send and Dashboard pages are placeholders until Milestones 2-5.

## Requirements

- macOS on Apple Silicon (built for an 8 GB M3), [Homebrew](https://brew.sh)
- Nothing else: `setup.sh` installs `uv` (which fetches Python 3.11) and Ollama if missing
- No paid services and no API keys. The only external account is your own Gmail.

## Setup and run

```bash
./setup.sh   # Python 3.11 venv, dependencies, ~/Reachout/, Ollama (brew) - safe to re-run
./run.sh     # starts the background worker and the UI at http://127.0.0.1:8501 ; Ctrl-C stops both
```

On first launch the **Setup Wizard** checks Ollama, pulls the model if missing (~2 GB for
`qwen2.5:3b-instruct`), and tests your Gmail SMTP/IMAP login.

### Gmail App Password

1. Use a **separate Gmail account** for cold outreach, not your primary one.
2. Turn on 2-Step Verification, then Google Account > Security > App passwords.
3. In Gmail settings enable IMAP (Forwarding and POP/IMAP).
4. Paste the 16-character password into Settings (or the wizard). It is stored in the **macOS
   Keychain** under the service name `Reachout`; it is never written to a file or the database.
   If macOS asks whether `python` may access the Keychain item, choose *Always Allow* so the
   background worker can read it.

## Where things live

| What | Where |
| --- | --- |
| Database, exports, logs | `~/Reachout/` (override with `REACHOUT_HOME`) |
| Worker log | `~/Reachout/logs/worker.log` |
| App Password | macOS Keychain, service `Reachout` |
| Non-secret settings | `settings` table in the SQLite DB |

The UI binds to `127.0.0.1` only and Streamlit usage telemetry is disabled.

## Safety rules (enforced in code, not just UI)

- Nothing sends without your approval (built in Milestones 3-4).
- The suppression list is always enforced; there is no UI to bypass it.
- The daily send cap is clamped to a hard ceiling of 100 whatever the setting says.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Tests use a temporary data folder and an in-memory keyring, so they never touch your real
`~/Reachout/` or Keychain.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Wizard says Ollama is not running | `brew services start ollama` (or `ollama serve`) |
| "Port 8501 is not available" | `./run.sh` is already running in another terminal |
| Sidebar says "Worker not running" | Start with `./run.sh`, not `streamlit run` directly |
| Gmail SMTP/IMAP login fails | Use an App Password (not your normal password), enable IMAP |
| Stale worker after a crash | `rm ~/Reachout/worker.pid` and re-run `./run.sh` |
