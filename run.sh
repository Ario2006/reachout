#!/usr/bin/env bash
# One-command run: starts the background worker, then the Streamlit UI.
# Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Run ./setup.sh first." >&2
  exit 1
fi

export PYTHONPATH="$PWD"
HOME_DIR="${REACHOUT_HOME:-$HOME/Reachout}"
mkdir -p "$HOME_DIR/logs"
PID_FILE="$HOME_DIR/worker.pid"

STARTED_WORKER=0
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Worker already running (pid $(cat "$PID_FILE"))."
else
  nohup .venv/bin/python -m app.worker >>"$HOME_DIR/logs/worker.log" 2>&1 &
  echo $! >"$PID_FILE"
  STARTED_WORKER=1
  echo "Worker started (pid $(cat "$PID_FILE")), log: $HOME_DIR/logs/worker.log"
fi

ST_PID=""
cleanup() {
  trap - EXIT INT TERM
  if [ -n "$ST_PID" ]; then kill "$ST_PID" 2>/dev/null || true; fi
  if [ "$STARTED_WORKER" = 1 ] && [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Run Streamlit in the background and wait, so a signal to this script runs the trap
# (bash defers traps while a foreground child is running).
.venv/bin/streamlit run app/main.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --browser.gatherUsageStats false &
ST_PID=$!
wait "$ST_PID"
