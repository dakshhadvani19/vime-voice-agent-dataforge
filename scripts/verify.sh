#!/usr/bin/env bash
# Everything that must be green before you push or record the demo (spec §22).
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
run() { printf '\n\033[1m▶ %s\033[0m\n' "$1"; shift; "$@" || { echo "  ✗ FAILED: $*"; fail=1; }; }

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

run "ruff (lint)"            "$PY" -m ruff check agent evaluation scripts
run "pytest (kernel + corpus)" "$PY" -m pytest -q
run "evaluation (deterministic A/B)" "$PY" evaluation/run_tests.py
if curl -sf -m 1 localhost:${AGENT_PORT:-8080}/health >/dev/null 2>&1; then
  run "reducer smoke (SSE → TS reducer)" bash -c 'cd frontend && npm run --silent smoke:reducer'
else
  printf '\n\033[33m! skipping reducer smoke (no dev server on :8080 — ./scripts/dev.sh starts one)\033[0m\n'
fi

if [ -d frontend/node_modules ]; then
  run "tsc --noEmit" bash -c 'cd frontend && npm run --silent typecheck'
  run "eslint" bash -c 'cd frontend && npm run --silent lint'
  run "vite build"   bash -c 'cd frontend && npm run --silent build'
else
  printf '\n\033[33m! skipping frontend checks (no frontend/node_modules — run npm install)\033[0m\n'
fi
if [ -n "${RIME_API_KEY:-}" ]; then
  run "rime voice proof" "$PY" scripts/proof_of_voice.py --repeat 2 --interrupt-at 250
else
  printf '\n\033[33m! RIME_API_KEY unset: skipping the live Rime proof (it prints SKIP)\033[0m\n'
fi

printf '\n'
[ "$fail" = 0 ] && printf '\033[32m✓ all checks green\033[0m\n' || printf '\033[31m✗ some checks failed (above)\033[0m\n'
exit $fail
