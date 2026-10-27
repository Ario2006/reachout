#!/usr/bin/env bash
# One command: unsigned Reachout.app + .dmg (arm64) with the Python backend bundled (PyInstaller).
#   scripts/build-mac.sh            -> dist/mac-arm64/Reachout.app and dist/Reachout-<version>-arm64.dmg
# Idempotent. Output goes to .logs/build-mac.log; only step names and the result are printed.
# The backend bundle is rebuilt only when backend/, scripts/backend_entry.py or requirements.txt changed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p .logs .build
LOG="$ROOT/.logs/build-mac.log"
: > "$LOG"
CURRENT="starting"
trap 'rc=$?; if [ $rc -ne 0 ]; then echo "FAILED during: $CURRENT (full log: $LOG)" >&2; tail -n 40 "$LOG" >&2; fi' EXIT
step() { CURRENT="$1"; printf '==> %s\n' "$1"; echo "=== $1" >> "$LOG"; }
run() { "$@" >> "$LOG" 2>&1; }
fail() { echo "$*" >&2; echo "$*" >> "$LOG"; exit 1; }

[ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ] || fail "build-mac.sh builds an arm64 macOS app and must run on an Apple Silicon Mac."

step "Node 24 (from .nvmrc)"
# shellcheck disable=SC1091
if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh"; nvm use >> "$LOG" 2>&1 || nvm install >> "$LOG" 2>&1 || true; fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo none)"
[ "$NODE_MAJOR" = "24" ] || fail "Node 24 LTS is required (found: $NODE_MAJOR). Install nvm and run 'nvm install' in the repo, or 'brew install node@24'."

command -v uv >/dev/null 2>&1 || fail "uv is required (scripts/setup.sh installs it: brew install uv)."

# ---- 1. Python backend bundle ------------------------------------------------------------------
PYI_VENV=".build/pyi-venv"
BUNDLE=".build/pyi-dist/reachout-backend"
SRC_HASH="$( (cat requirements.txt scripts/backend_entry.py; find backend -type f \( -name '*.py' -o -name '*.sql' \) | LC_ALL=C sort | xargs cat) | shasum -a 256 | cut -d' ' -f1)"

if [ -x "$BUNDLE/reachout-backend" ] && [ "$(cat .build/backend.hash 2>/dev/null)" = "$SRC_HASH" ]; then
  step "Backend bundle is up to date (skipping PyInstaller)"
else
  step "Python 3.11 build environment (.build/pyi-venv)"
  if [ ! -x "$PYI_VENV/bin/python" ] || ! "$PYI_VENV/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'; then
    rm -rf "$PYI_VENV"
    run uv venv --python 3.11 "$PYI_VENV"
  fi
  # Runtime dependencies only (no pytest/ruff/...), pinned to the versions the tests run against when .venv exists.
  awk '/^# test tooling/{exit} !/^pytest/ && !/^#/ && NF' requirements.txt > .build/requirements-runtime.txt
  CONSTRAINT=()
  if [ -x .venv/bin/python ]; then
    uv pip freeze --python .venv/bin/python > .build/constraints.txt 2>> "$LOG"
    CONSTRAINT=(-c .build/constraints.txt)
  fi
  run uv pip install --python "$PYI_VENV/bin/python" ${CONSTRAINT[@]+"${CONSTRAINT[@]}"} -r .build/requirements-runtime.txt "pyinstaller>=6.10"

  step "Bundling the backend with PyInstaller (api + worker in one executable)"
  rm -rf "$BUNDLE" .build/pyi-work
  run "$PYI_VENV/bin/pyinstaller" --noconfirm --clean --onedir --name reachout-backend \
    --distpath .build/pyi-dist --workpath .build/pyi-work --specpath .build \
    --paths "$ROOT" \
    --add-data "$ROOT/backend/core/migrations:backend/core/migrations" \
    --collect-submodules backend --collect-submodules uvicorn --collect-submodules keyring --collect-submodules apscheduler \
    --collect-all trafilatura --collect-all courlan --collect-all htmldate --collect-all justext --collect-all tld --collect-all ddgs \
    --collect-data certifi --collect-data tzdata \
    --copy-metadata keyring --copy-metadata apscheduler \
    --exclude-module tkinter --exclude-module pytest --exclude-module hypothesis \
    scripts/backend_entry.py
  rm -f .build/backend.hash

  step "Checking the bundled backend (selfcheck, /health, worker, migrations)"
  BIN="$ROOT/$BUNDLE/reachout-backend"
  run "$BIN" selfcheck || fail "The bundled backend failed its selfcheck (see log)."
  SMOKE_HOME="$(mktemp -d)"; TOKEN="$(printf 'b%.0s' $(seq 1 64))"
  PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
  export REACHOUT_HOME="$SMOKE_HOME" REACHOUT_API_TOKEN="$TOKEN"
  "$BIN" api --port "$PORT" >> "$LOG" 2>&1 & API_PID=$!
  "$BIN" worker >> "$LOG" 2>&1 & WORKER_PID=$!
  ok=0
  for _ in $(seq 1 40); do
    if curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"schema_version":5'; then ok=1; break; fi
    sleep 0.5
  done
  sleep 3  # let the worker run a few ticks
  HB="$(sqlite3 "$SMOKE_HOME/reachout.db" 'select count(*) from worker_heartbeat' 2>/dev/null || echo 0)"
  kill -TERM "$API_PID" "$WORKER_PID" 2>/dev/null || true
  wait "$API_PID" "$WORKER_PID" 2>/dev/null || true
  rm -rf "$SMOKE_HOME"; unset REACHOUT_HOME REACHOUT_API_TOKEN
  [ "$ok" = 1 ] || fail "Bundled API did not answer /health with the migrated schema."
  [ "$HB" -ge 1 ] || fail "Bundled worker never wrote a heartbeat."
  echo "$SRC_HASH" > .build/backend.hash
fi

# ---- 2. Icon (committed; regenerated only if missing) --------------------------------------------
if [ ! -f desktop/build/icon.icns ]; then
  step "Placeholder icon"
  run python3 desktop/build/make-icon.py
fi

# ---- 3. Electron app + dmg ---------------------------------------------------------------------
cd desktop
if [ ! -x node_modules/.bin/electron-builder ] || [ -L node_modules ]; then
  step "Desktop dependencies (npm ci)"
  [ -L node_modules ] && rm node_modules
  run npm ci
fi
step "Building the renderer and main process"
run npm run build
step "Packaging Reachout.app and the dmg (electron-builder, unsigned)"
rm -rf "$ROOT/dist"
run env CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder --mac --arm64 --publish never
cd "$ROOT"

APP="$(ls -d dist/mac-arm64/Reachout.app 2>/dev/null || true)"
DMG="$(ls dist/*.dmg 2>/dev/null | head -n1 || true)"
[ -n "$APP" ] && [ -n "$DMG" ] || fail "electron-builder finished but the .app or .dmg is missing."
CURRENT="done"
echo
echo "Built (unsigned, arm64):"
printf '  %s  %s\n' "$(du -sh "$APP" | cut -f1)" "$ROOT/$APP"
printf '  %s  %s\n' "$(du -sh "$DMG" | cut -f1)" "$ROOT/$DMG"
echo "First launch: right-click > Open, or: xattr -dr com.apple.quarantine /Applications/Reachout.app"
