#!/usr/bin/env bash
# Development mode: hot-reloading renderer, and the API restarts on backend code changes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -x .venv/bin/python ] || { echo "Run scripts/setup.sh first." >&2; exit 1; }
# shellcheck disable=SC1091
if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh"; nvm use >/dev/null; fi

cd desktop
exec npm run dev
