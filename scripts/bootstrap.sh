#!/usr/bin/env bash
# One command from clone to running app.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "── Break My Voice Agent · setup ─────────────────────────────────────────"
[ -f .env ] || { cp .env.example .env; echo "  wrote .env from .env.example — add RIME_API_KEY"; }

if command -v python3 >/dev/null; then
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip install -q --disable-pip-version-check -r agent/requirements-dev.txt
  echo "  ✓ venv with pytest+ruff (kernel needs no runtime deps)"
  if [ "${INSTALL_LIVE:-1}" = "1" ]; then
    ./.venv/bin/pip install -q --disable-pip-version-check -r agent/requirements-live.txt \
      && echo "  ✓ livekit + rime stack installed" \
      || echo "  ! live stack failed — the lab and evaluator still run offline"
  fi
fi

if command -v npm >/dev/null; then
  (cd frontend && npm install --silent --no-fund --no-audit) && echo "  ✓ frontend deps"
fi

PY=./.venv/bin/python; [ -x "$PY" ] || PY=python3
"$PY" -m pytest -q || echo "  (tests reported above)"
"$PY" evaluation/run_tests.py >/dev/null && echo "  ✓ evaluation ran; see evaluation/results/latest.md"
echo
echo "Run everything:   ./scripts/dev.sh"
echo "Or piecewise:     .venv/bin/python agent/dev_server.py  +  cd frontend && npm run dev"
