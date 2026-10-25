#!/usr/bin/env bash
# One-command setup: Python 3.11 venv (uv), Node 24 deps, data folder, Ollama.
# Safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOME_DIR="${REACHOUT_HOME:-$HOME/Reachout}"
say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

command -v brew >/dev/null 2>&1 || { echo "Homebrew is required: https://brew.sh" >&2; exit 1; }

# ---- Python backend ---------------------------------------------------------
command -v uv >/dev/null 2>&1 || { say "Installing uv"; brew install uv; }

say "Python 3.11 virtualenv (.venv)"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'; then
  rm -rf .venv
  uv venv --python 3.11 .venv
fi
# A venv from the old Streamlit milestone still has streamlit installed: rebuild it clean.
if .venv/bin/python -c 'import streamlit' >/dev/null 2>&1; then
  say "Removing the old Streamlit environment"
  rm -rf .venv
  uv venv --python 3.11 .venv
fi
uv pip install --python .venv/bin/python -r requirements.txt

# ---- Node / Electron --------------------------------------------------------
say "Node 24 (from .nvmrc)"
# shellcheck disable=SC1091
if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh"; nvm install >/dev/null; nvm use >/dev/null; fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo none)"
if [ "$NODE_MAJOR" != "24" ]; then
  echo "Node 24 LTS is required (found: ${NODE_MAJOR}). Install nvm (https://github.com/nvm-sh/nvm) or run: brew install node@24" >&2
  exit 1
fi

say "Desktop dependencies"
( cd desktop && npm ci )
say "Electron binary (downloaded once)"
( cd desktop && npx electron --version >/dev/null )
say "Building the app"
( cd desktop && npm run build >/dev/null )

# ---- Data folder + Ollama ---------------------------------------------------
say "Data folder: $HOME_DIR"
mkdir -p "$HOME_DIR/exports" "$HOME_DIR/logs"

say "Ollama"
command -v ollama >/dev/null 2>&1 || brew install ollama
if ! curl -fs -m 2 http://127.0.0.1:11434/ >/dev/null 2>&1; then
  echo "Starting Ollama as a background service (brew services)"
  brew services start ollama >/dev/null
  for _ in $(seq 1 20); do curl -fs -m 2 http://127.0.0.1:11434/ >/dev/null 2>&1 && break; sleep 1; done
fi
if curl -fs -m 2 http://127.0.0.1:11434/ >/dev/null 2>&1; then
  echo "Ollama is running."
else
  echo "Ollama did not start; run 'ollama serve' in another terminal." >&2
fi

say "Done. Start the app with: scripts/run.sh   (the setup wizard downloads the model on first launch)"
