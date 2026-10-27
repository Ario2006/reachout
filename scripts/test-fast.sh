#!/usr/bin/env bash
# T1 inner loop: only what the working-tree changes touch. Output goes to .logs/, only failures are shown.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p .logs
PY=.venv/bin/python
fail=0
changed=$( (git diff --name-only HEAD; git ls-files --others --exclude-standard) | sort -u)
py_changed=$(echo "$changed" | grep -E '\.py$' || true)
fe_changed=$(echo "$changed" | grep -E '^desktop/src/.*\.(ts|tsx)$' | sed 's|^desktop/||' || true)

if [ -n "$py_changed" ] && $PY -m ruff --version >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  $PY -m ruff check $py_changed > .logs/ruff.log 2>&1 || { echo "ruff: FAIL"; tail -n 20 .logs/ruff.log; fail=1; }
fi
$PY -m pytest --testmon -q -x -m 'not slow' -p no:cacheprovider > .logs/t1-py.log 2>&1 || { echo "pytest: FAIL"; tail -n 30 .logs/t1-py.log; fail=1; }
echo "pytest: $(tail -n 1 .logs/t1-py.log)"
if [ -n "$fe_changed" ]; then
  # shellcheck disable=SC2086
  (cd desktop && npx vitest related $fe_changed --run) > .logs/t1-fe.log 2>&1 || { echo "vitest: FAIL"; tail -n 30 .logs/t1-fe.log; fail=1; }
  (cd desktop && npx tsc --noEmit --incremental -p tsconfig.web.json) > .logs/t1-tsc.log 2>&1 || { echo "tsc: FAIL"; tail -n 20 .logs/t1-tsc.log; fail=1; }
  echo "frontend: checked $(echo "$fe_changed" | wc -l | tr -d ' ') changed file(s)"
fi
exit $fail
