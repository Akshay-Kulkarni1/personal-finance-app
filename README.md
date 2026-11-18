# ahead-upgrade-from-v4

**Banks show your past. Ahead helps you see what's next.**

**Project:** `ahead-upgrade-from-v4`  
**Product:** Ahead  
**Latest release:** Phase 0 · Milestone 8 (credential-free verification and explicit production release gate)

This cumulative release includes Milestones 1–8. **Development-stage only; not production-ready for real customer finances or money movement.**

## What works

- User-owned sign-in and sessions with Argon2id passwords, HttpOnly cookies, CSRF checks and origin restrictions.
- Email verification, password reset, optional authenticator MFA and recovery codes, security settings and session management.
- Bank linking using Plaid Link/Sandbox, read-only connected accounts and transactions. No personal bank account is required during development.
- Durable bank synchronization worker, verified Plaid webhooks, retries, reconnect flow, transaction reconciliation and encrypted server-held bank tokens.
- Additive, version-tracked user data migrations, SQLite online backup, isolated restore drill, health/readiness and token-protected worker status.
- Same-origin frontend API proxy and startup readiness check to prevent opaque "Failed to fetch" errors.
- Reauthenticated user data export and active-database account deletion after explicit bank disconnection.
- Strict production configuration checks and local HTTP + browser acceptance-test scripts; a disposable PostgreSQL migration/isolation smoke test; worker heartbeat and monitoring in the protected operations endpoint. Milestone 7 adds account-wide login throttles, browser session limits, bounded sync failures, atomic cursor updates and credential-free staging options. Milestone 8 adds a reproducible offline verification runner and stronger production PostgreSQL TLS/location checks.
- Separate legacy Maya demo, disabled for personal accounts and production.

## Run Ahead locally

Extract this ZIP and open a terminal in the `ahead-upgrade-from-v4` directory.

**macOS/Linux:**

```bash
./scripts/start-local.sh --setup   # first run: install dependencies
./scripts/start-local.sh           # later runs
```

**Windows PowerShell:**

```powershell
.\scripts\start-local.ps1 -Setup   # first run
.\scripts\start-local.ps1          # later runs
```

The launcher starts the frontend, backend and sync worker. If it creates a new `.env`, it generates a stable local encryption key. Do not delete or change that key if you have encrypted Plaid connections/MFA secrets.

- **Ahead:** http://127.0.0.1:3000/login
- **Same-origin API check:** http://127.0.0.1:3000/api/ahead/health
- **API docs (development only):** http://127.0.0.1:8000/docs
- **Readiness:** http://127.0.0.1:8000/ready
- **Troubleshoot:** `./scripts/doctor-local.sh`
- **HTTP lifecycle test:** `python3 scripts/smoke-local.py` (in a second terminal)

Register an account and open **Security settings** (shield icon) for email verification and MFA. Without SMTP configured, development email messages are stored privately in `backend/data/mail-outbox/`. Password recovery is linked from Sign in. In **Security settings**, follow **Data & privacy** to export your information or delete an account after disconnecting its banks. Open **Banks** to connect a Plaid Sandbox institution after adding your own Sandbox developer credentials to `backend/.env`.

The URLs above work **on the machine running the launcher**; this ZIP does not include an internet-accessible deployment.

## Development references

- [Phase 0 checklist](PHASE0.md)
- [Milestone 2: bank sync](PHASE0-MILESTONE-2.md)
- [Milestone 3: security, migrations and local setup](PHASE0-MILESTONE-3.md)
- [Milestone 4: reliability, ops monitoring and restore](PHASE0-MILESTONE-4.md)
- [Milestone 5: privacy, production checks and HTTP diagnostics](PHASE0-MILESTONE-5.md)
- [Milestone 6: Chromium, PostgreSQL verification and worker heartbeat](PHASE0-MILESTONE-6.md)
- [Milestone 7: Security, bounded retries and staging](PHASE0-MILESTONE-7.md)
- [Milestone 8: Offline verification and release gating](PHASE0-MILESTONE-8.md)
- [Open Phase 0 production gates](PHASE0-PENDING.md)
- [Release index](MILESTONES.md)
- [Local configuration example](backend/.env.example)

Backend test command: `cd backend && .venv/bin/python -m pytest -q tests`. **125 backend tests pass**, including the new version 5 migration and heartbeat checks. A direct real FastAPI HTTP lifecycle smoke test also passed. The Next.js lint/build/Chromium journey and PostgreSQL container tests are **configured but not executed here** (npm registry was unreachable and local PostgreSQL unavailable). See [Milestone 8](PHASE0-MILESTONE-8.md) and [the pending checklist](PHASE0-PENDING.md) for verified evidence, open dependencies and Phase 1 handoff.

**Important:** The legacy forecast, budgeting, transfer and subscription actions use shared demonstration data and are not enabled for linked personal accounts. Public launch requires the remaining Phase 0 security, privacy, operational and financial-correctness work.

## Repeatable credential-free verification

Run `./scripts/verify-phase0-offline.sh` after backend setup. It runs backend tests, real local HTTP registration/privacy, worker heartbeat/operator status and SQLite restore against **temporary test-only data**. No Plaid credentials or real bank account needed. This does **not** establish frontend, PostgreSQL staging or Plaid readiness.

## Phase 0 Sandbox follow-up

The project owner confirmed the local Plaid Sandbox happy path (14 fictional accounts, balances/transactions, repeat sync, reconnect, disconnect and sign conversion). No Plaid credentials are included in this source release. Read `PHASE0-VERIFICATION-UPDATE.md` for the evidence and `PHASE0-PENDING.md` for production blockers such as signed webhooks, full Chromium testing, PostgreSQL staging and independent security review.

Plaid Sandbox callback testing: when a public staging HTTPS `PLAID_WEBHOOK_URL` is configured, the Banks page provides **Register HTTPS webhook** (also for existing Items) and **Send test webhook** (Sandbox only). The latter only requests the event; receipt and processing require separate verification. Follow `PHASE0-VERIFICATION-UPDATE.md`.
