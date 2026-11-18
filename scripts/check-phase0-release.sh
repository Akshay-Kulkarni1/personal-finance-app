#!/usr/bin/env bash
# Strict LOCAL source/build/security gate. Does not certify external staging.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${AHEAD_VERIFY_PYTHON:-python3}"
if [[ "$PY" != */* ]]; then PY="$(command -v "$PY" || true)"; fi
if [[ -z "$PY" || ! -x "$PY" ]]; then
  echo 'Python interpreter unavailable (set AHEAD_VERIFY_PYTHON).' >&2
  exit 2
fi
if ! command -v npm >/dev/null || ! command -v node >/dev/null; then
  echo 'Node.js and npm are required for the release gate.' >&2
  exit 2
fi
printf '\n[1/5] Git repository safety policy\n'
"$PY" "$ROOT/scripts/check-repo-safety.py"
printf '\n[2/5] Backend tests + private HTTP/worker/recovery acceptance\n'
AHEAD_VERIFY_PYTHON="$PY" "$ROOT/scripts/verify-phase0-offline.sh"
printf '\n[3/5] Install exact locked frontend dependencies\n'
(cd "$ROOT/frontend" && npm ci)
printf '\n[4/5] Require Next.js 16 and passing lint/production build\n'
(cd "$ROOT/frontend" && node - <<'NODE'
const version = require('next/package.json').version;
if (!version.startsWith('16.')) {
  console.error(`Blocked: Next.js ${version} still installed; upgrade and regenerate package-lock.json first.`);
  process.exit(1);
}
console.log(`Next.js ${version}`);
NODE
npm run lint && npm run build)
printf '\n[5/5] Reject unresolved npm advisories\n'
(cd "$ROOT/frontend" && npm audit --omit=dev && npm audit)
printf '\nPASS: local Phase 0 release checks.\n'
printf 'NOT VERIFIED by this script: hosted TLS/SMTP/monitoring, signed Plaid webhook delivery, managed PostgreSQL PITR, independent security and accessibility reviews.\n'
