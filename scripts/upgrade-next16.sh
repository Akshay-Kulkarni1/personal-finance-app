#!/usr/bin/env bash
# Deliberate, LOCAL Next.js 16 upgrade with audit and test gates.
# Run only on a separate Git branch from a clean working tree. It does not commit.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v npm >/dev/null || ! command -v node >/dev/null; then
  echo 'Node and npm are required.' >&2
  exit 2
fi
node - <<'NODE'
const [maj, min] = process.versions.node.split('.').map(Number);
if (maj < 20 || (maj === 20 && min < 9)) {
  console.error('Next.js 16 requires Node.js 20.9 or later.'); process.exit(2);
}
NODE
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  BRANCH="$(git -C "$ROOT" branch --show-current)"
  if [[ "$BRANCH" == 'main' || "$BRANCH" == 'master' || -z "$BRANCH" ]]; then
    echo 'Create a separate feature branch before updating dependencies.' >&2
    exit 2
  fi
  if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]]; then
    echo 'Working tree is not clean. Commit or safely stash your changes first.' >&2
    exit 2
  fi
fi
cd "$ROOT/frontend"
echo 'Updating Next.js and matching ESLint config. npm will regenerate package-lock.json.'
npm install --save-exact next@16.4.6 react@19.2.0 react-dom@19.2.0
npm install --save-dev --save-exact eslint-config-next@16.4.6
npm run lint
npm run build
echo 'Checking production dependency advisories...'
npm audit --omit=dev
echo 'Checking all dependencies (including build and lint tooling)...'
npm audit
echo 'PASS: Next.js 16 dependency migration, lint, build and npm audits.'
echo 'Next: run browser, backend and PostgreSQL CI before merging. No commit was created.'
