#!/usr/bin/env bash
# Lab + token server on :8080, Vite on :5173. Add --agent to also start the LiveKit worker.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
trap 'kill 0' EXIT
"$PY" agent/dev_server.py &
(cd frontend && npm run dev) &
if [ "${1:-}" = "--agent" ]; then
  LIVEKIT_DEV=1 "$PY" agent/main.py dev &
fi
wait
