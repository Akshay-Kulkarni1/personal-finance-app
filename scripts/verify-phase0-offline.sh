#!/usr/bin/env bash
# Phase 0 Milestone 8: reproducible local verification, with NO personal bank
# credentials or live customer data. All mutable state is in a temp directory.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${AHEAD_VERIFY_PYTHON:-$ROOT/backend/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  echo 'Missing backend venv. First run ./scripts/start-local.sh --setup' >&2
  exit 2
fi
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ahead-phase0-check.XXXXXXXX")"
API_PID=''
cleanup() {
  if [[ -n "$API_PID" ]]; then kill "$API_PID" 2>/dev/null || true; wait "$API_PID" 2>/dev/null || true; fi
  rm -rf "$TMP"
}
trap cleanup EXIT
export APP_DB_PATH="$TMP/check.sqlite3"
export DEV_MAIL_OUTBOX="$TMP/outbox"
export AHEAD_ENV='development'
export AHEAD_DEMO_MODE='false'
# Do not override the provider flag during mock-based tests; those fixtures
# exercise signed webhooks with fake provider responses.
unset AHEAD_BANK_CONNECTIONS_ENABLED || true
export AHEAD_ENCRYPTION_KEY="$($PY -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export AHEAD_OPS_TOKEN="$($PY -c 'import secrets; print(secrets.token_urlsafe(40))')"
unset APP_DATABASE_URL SMTP_HOST PLAID_CLIENT_ID PLAID_SECRET || true
PORT="$($PY - <<'PY'
import socket
with socket.socket() as s:
    s.bind(('127.0.0.1',0))
    print(s.getsockname()[1])
PY
)"
echo 'CHECK 1: backend regression tests (isolated local state)'
(cd "$ROOT/backend" && "$PY" -m pytest -q tests)
echo 'CHECK 2: real loopback API + worker + privacy lifecycle'
export AHEAD_BANK_CONNECTIONS_ENABLED='false'
(cd "$ROOT/backend" && exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" --no-access-log) > "$TMP/api.log" 2>&1 & API_PID=$!
if ! "$PY" "$ROOT/scripts/smoke-local.py" --direct --base "http://127.0.0.1:$PORT" --wait 30; then
  echo 'FAIL: private loopback API test; no customer data used' >&2
  exit 1
fi
(cd "$ROOT/backend" && "$PY" worker.py --once)
AHEAD_OPS_URL="http://127.0.0.1:$PORT/ops/status" "$PY" "$ROOT/scripts/check-ops.py" --url "http://127.0.0.1:$PORT/ops/status"
echo 'CHECK 3: SQLite backup and isolated restore'
(cd "$ROOT/backend" && "$PY" restore_drill.py)
echo 'PASS: offline Phase 0 checks. Plaid Sandbox happy path was user-verified separately; NOT VERIFIED HERE: Next.js browser, PostgreSQL staging, live Plaid webhooks, hosted ops, independent security review.'
