#!/usr/bin/env bash
# T3 merge gate (lead only): full suite, typecheck, lint, safety coverage. Quiet; prints one summary line plus failures.
# Flags: --e2e adds the Playwright smoke test (final pass and before packaging only); --live adds @live tests.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p .logs
PY=.venv/bin/python
e2e=0; live=(-m 'not live')
for a in "$@"; do [ "$a" = "--e2e" ] && e2e=1; [ "$a" = "--live" ] && live=(); done
start=$(date +%s); bad=()

$PY -m pytest -q -n 2 -p no:cacheprovider "${live[@]}" --durations=5 --durations-min=1.0 \
  --cov=backend --cov-report=json:.logs/coverage.json --cov-report= > .logs/t3-py.log 2>&1 || bad+=("pytest")
(cd desktop && npx vitest run) > .logs/t3-fe.log 2>&1 || bad+=("vitest")
(cd desktop && npm run -s typecheck) > .logs/t3-tsc.log 2>&1 || bad+=("typecheck")
(cd desktop && npm run -s lint) > .logs/t3-eslint.log 2>&1 || bad+=("eslint")
if $PY -m ruff --version >/dev/null 2>&1; then $PY -m ruff check backend tests > .logs/t3-ruff.log 2>&1 || bad+=("ruff"); fi
$PY scripts/check_coverage.py .logs/coverage.json > .logs/t3-cov.log 2>&1 || bad+=("coverage")
(cd desktop && npm run -s gen:api -- --check) > .logs/t3-api.log 2>&1 || bad+=("openapi-drift")
if [ $e2e -eq 1 ]; then (cd desktop && npm run -s test:e2e) > .logs/t3-e2e.log 2>&1 || bad+=("playwright"); fi

secs=$(( $(date +%s) - start ))
echo "py: $(grep -E '[0-9]+ (passed|failed)' .logs/t3-py.log | tail -n 1) | fe: $(grep -E 'Tests ' .logs/t3-fe.log | tail -n 1 | sed 's/^ *//') | ${secs}s"
grep -E '^(FAILED|ERROR) ' .logs/t3-py.log | head -n 20
grep -E '^ *(FAIL|×) ' .logs/t3-fe.log | head -n 20
grep -E 'slowest|^[0-9.]+s (call|setup)' .logs/t3-py.log | head -n 6
if [ ${#bad[@]} -eq 0 ]; then echo "GATE: PASS"; else echo "GATE: FAIL (${bad[*]})  logs in .logs/"; for b in "${bad[@]}"; do case $b in coverage) tail -n 8 .logs/t3-cov.log;; typecheck) tail -n 8 .logs/t3-tsc.log;; eslint) tail -n 8 .logs/t3-eslint.log;; ruff) tail -n 8 .logs/t3-ruff.log;; openapi-drift) tail -n 4 .logs/t3-api.log;; esac; done; exit 1; fi
