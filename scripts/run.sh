#!/usr/bin/env bash
# Build (fast) and open the desktop app. Electron starts and supervises the API and worker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -x .venv/bin/python ] || { echo "Run scripts/setup.sh first." >&2; exit 1; }
# shellcheck disable=SC1091
if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh"; nvm use >/dev/null; fi
[ -d desktop/node_modules ] || { echo "Run scripts/setup.sh first." >&2; exit 1; }

cd desktop
npm run build >/dev/null
exec npx electron-vite preview
