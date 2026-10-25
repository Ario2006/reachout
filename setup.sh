#!/usr/bin/env bash
# One-command setup: Python 3.11 venv (via uv), dependencies, data folder, Ollama.
set -euo pipefail
cd "$(dirname "$0")"

HOME_DIR="${REACHOUT_HOME:-$HOME/Reachout}"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv"
  brew install uv
fi

say "Creating Python 3.11 virtualenv (.venv)"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'; then
  rm -rf .venv
  uv venv --python 3.11 .venv
fi

say "Installing dependencies"
uv pip install --python .venv/bin/python -r requirements.txt

say "Creating data folder at $HOME_DIR"
mkdir -p "$HOME_DIR/exports" "$HOME_DIR/logs"

say "Checking Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  brew install ollama
fi
if ! curl -fs -m 2 http://127.0.0.1:11434/ >/dev/null 2>&1; then
  echo "Starting Ollama as a background service (brew services)"
  brew services start ollama >/dev/null
  for _ in $(seq 1 20); do
    curl -fs -m 2 http://127.0.0.1:11434/ >/dev/null 2>&1 && break
    sleep 1
  done
fi
if curl -fs -m 2 http://127.0.0.1:11434/ >/dev/null 2>&1; then
  echo "Ollama is running."
else
  echo "Ollama did not start; run 'ollama serve' in another terminal." >&2
fi

say "Done. Next: ./run.sh (the setup wizard will pull the model if it is missing)"
