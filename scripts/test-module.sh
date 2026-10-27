#!/usr/bin/env bash
# T2 before a subagent reports done: unit tests for the owned modules only.
# Usage: scripts/test-module.sh compose drafts      (matches tests/test_<name>*.py and desktop/src/features/<name>/)
set -uo pipefail
cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { echo "usage: $0 <module>..."; exit 2; }
mkdir -p .logs
PY=.venv/bin/python
fail=0; py=(); fe=()
for m in "$@"; do
  for f in tests/test_${m}*.py; do [ -e "$f" ] && py+=("$f"); done
  [ -d "desktop/src/features/$m" ] && fe+=("src/features/$m")
done
if [ ${#py[@]} -gt 0 ]; then
  $PY -m pytest -q -x -p no:cacheprovider "${py[@]}" > .logs/t2-py.log 2>&1 || { echo "pytest: FAIL"; tail -n 30 .logs/t2-py.log; fail=1; }
  echo "pytest: $(tail -n 1 .logs/t2-py.log)"
fi
if [ ${#fe[@]} -gt 0 ]; then
  (cd desktop && npx vitest run "${fe[@]}") > .logs/t2-fe.log 2>&1 || { echo "vitest: FAIL"; tail -n 30 .logs/t2-fe.log; fail=1; }
  echo "vitest: $(grep -E 'Tests ' .logs/t2-fe.log | tail -n 1)"
fi
[ ${#py[@]} -eq 0 ] && [ ${#fe[@]} -eq 0 ] && { echo "no tests found for: $*"; fail=1; }
exit $fail
