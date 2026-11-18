#!/usr/bin/env bash
# Strict configuration gate: never launch an unsafe production API.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"
python production_check.py
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-access-log
