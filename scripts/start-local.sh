#!/usr/bin/env bash
# Ahead local development launcher. Keeps API / web / worker on this machine.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PY="$BACKEND/.venv/bin/python"
if [[ "${1:-}" == "--setup" ]]; then
  if ! command -v npm >/dev/null || ! command -v node >/dev/null; then
    echo "Node.js/npm missing. Install Node.js LTS from https://nodejs.org, then retry." >&2
    exit 1
  fi
  python3 -m venv "$BACKEND/.venv"
  "$PY" -m pip install -r "$BACKEND/requirements.txt"
  (cd "$FRONTEND" && npm ci)
fi
if [[ ! -x "$PY" || ! -d "$FRONTEND/node_modules" ]]; then
  echo "Dependencies missing. Run: ./scripts/start-local.sh --setup" >&2
  exit 1
fi
"$PY" "$ROOT/scripts/configure-local.py"
"$PY" "$ROOT/scripts/doctor-local.py" --preflight
LOGS="$BACKEND/data/logs"
mkdir -p "$LOGS"
chmod 700 "$LOGS"
cleanup() {
  for pid in "${WEB_PID:-}" "${WORKER_PID:-}" "${API_PID:-}"; do
    if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
(cd "$BACKEND" && exec "$PY" -m uvicorn main:app --reload --host 127.0.0.1 --port 8000) >"$LOGS/api.log" 2>&1 & API_PID=$!
if ! "$PY" "$ROOT/scripts/doctor-local.py" --wait-api --timeout 25; then
  echo "API startup log:" >&2
  tail -n 45 "$LOGS/api.log" >&2 || true
  exit 1
fi
(cd "$BACKEND" && exec "$PY" worker.py --poll 30) >"$LOGS/worker.log" 2>&1 & WORKER_PID=$!
(cd "$FRONTEND" && exec npm run dev -- --hostname 127.0.0.1 --port 3000) & WEB_PID=$!
echo ""
echo "Ahead — local development"
echo " Website:   http://127.0.0.1:3000 (or http://localhost:3000)"
echo " API proxy: http://127.0.0.1:3000/api/ahead/health"
echo " API docs:  http://127.0.0.1:8000/docs"
echo " Diagnose:  ./scripts/doctor-local.sh"
echo " Verify:    python3 scripts/smoke-local.py  (from a second terminal)"
echo " Logs:      backend/data/logs/"
echo " Email outbox: backend/data/mail-outbox/"
echo " Press Ctrl+C to stop. Do not expose this development server publicly."
wait "$WEB_PID"
