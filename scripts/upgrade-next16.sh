#!/usr/bin/env bash
# Ahead v5: controlled Next.js 16 upgrade. Run from a clean feature branch.
# Never commits, pushes, resets, or touches backend secrets/data.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$ROOT/frontend"

if [[ ! -f "$FRONTEND/package.json" || ! -f "$FRONTEND/eslint.config.mjs" ]]; then
  echo 'ERROR: Expected frontend/package.json and frontend/eslint.config.mjs.' >&2
  exit 2
fi
for command_name in node npm git; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: Required command not found: $command_name" >&2
    exit 2
  fi
done
node - <<'NODE'
const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 20 || (major === 20 && minor < 9)) {
  console.error('Next.js 16 requires Node.js 20.9 or later.');
  process.exit(2);
}
NODE
if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo 'ERROR: Run from a Git worktree.' >&2
  exit 2
fi
branch="$(git -C "$ROOT" branch --show-current)"
if [[ -z "$branch" || "$branch" == 'main' || "$branch" == 'master' ]]; then
  echo 'ERROR: Create/switch to a feature branch before running the upgrade.' >&2
  exit 2
fi
# Ignored files such as backend/.env are fine; reject changes to tracked files.
if ! git -C "$ROOT" diff --quiet || ! git -C "$ROOT" diff --cached --quiet; then
  echo 'ERROR: Commit or safely stash tracked changes before upgrading.' >&2
  exit 2
fi

cd "$FRONTEND"
if ! grep -q 'FlatCompat' eslint.config.mjs && ! grep -q 'eslint-config-next/core-web-vitals' eslint.config.mjs; then
  echo 'ERROR: ESLint config is customized; review it before overwriting.' >&2
  exit 2
fi

echo '[1/7] Installing supported Next.js 16 + matching React runtime'
# Use explicit stable versions to avoid a later untested React major change.
npm install --save-exact next@16.4.6 react@19.3.1 react-dom@19.3.1

echo '[2/7] Updating Next.js ESLint preset and React TypeScript definitions'
npm install --save-dev --save-exact eslint-config-next@16.4.6 @types/react@latest @types/react-dom@latest
npm uninstall --save-dev @eslint/eslintrc

echo '[3/7] Migrating ESLint to official Next.js 16 flat presets'
if grep -q 'FlatCompat' eslint.config.mjs; then
  cat > eslint.config.mjs <<'EOF'
import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    'node_modules/**',
    '.next/**',
    'out/**',
    'build/**',
    'next-env.d.ts',
  ]),
]);
EOF
fi

echo '[4/7] Reinstalling exactly from the regenerated lockfile'
npm ci

echo '[5/7] Checking the framework version, lint, and production build'
node - <<'NODE'
const next = require('next/package.json').version;
const eslintConfig = require('eslint-config-next/package.json').version;
if (next !== '16.4.6' || eslintConfig !== next) {
  console.error(`Unexpected versions: Next ${next}, eslint-config-next ${eslintConfig}`);
  process.exit(1);
}
console.log(`PASS: Next.js ${next} and eslint-config-next ${eslintConfig}`);
NODE
npm run lint
npm run build

echo '[6/7] Checking production dependencies (no outstanding advisories)'
npm audit --omit=dev

echo '[7/7] Checking all dependencies, including development tooling'
npm audit

printf '\nPASS: Next.js 16 installation, lint, build and both npm audits.\n'
printf 'Next: run backend tests and GitHub Actions browser/PostgreSQL jobs before merging.\n'
printf 'Files to review: frontend/package.json, frontend/package-lock.json, frontend/eslint.config.mjs.\n'
