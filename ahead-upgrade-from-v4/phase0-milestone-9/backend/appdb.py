"""User-scoped application store, independent of Ahead's legacy shared demo ledger.

Local development uses SQLite; APP_DATABASE_URL selects PostgreSQL for deployment.
Every user data table includes user_id. No financial data falls back to a demo persona.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from config import ROOT

INITIALIZED = set()
INIT_LOCK = threading.Lock()

DDL = [
    """CREATE TABLE IF NOT EXISTS app_users (
        id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        created_at BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS app_sessions (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        csrf TEXT NOT NULL, expires_at BIGINT NOT NULL, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS app_sessions_user ON app_sessions(user_id)",
    """CREATE TABLE IF NOT EXISTS app_schema_migrations (
        version INTEGER PRIMARY KEY, applied_at BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS app_login_attempts (
        key TEXT PRIMARY KEY, count INTEGER NOT NULL, start_at BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS app_bank_connections (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        item_id TEXT NOT NULL UNIQUE, encrypted_token TEXT NOT NULL, institution_id TEXT,
        institution_name TEXT, cursor TEXT, status TEXT NOT NULL,
        created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS app_bank_connections_user ON app_bank_connections(user_id)",
    """CREATE TABLE IF NOT EXISTS app_bank_accounts (
        user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        id TEXT NOT NULL, connection_id TEXT NOT NULL REFERENCES app_bank_connections(id) ON DELETE CASCADE,
        name TEXT NOT NULL, mask TEXT, type TEXT, subtype TEXT, currency TEXT,
        current_balance DOUBLE PRECISION, available_balance DOUBLE PRECISION,
        updated_at BIGINT NOT NULL, PRIMARY KEY (user_id, id))""",
    """CREATE TABLE IF NOT EXISTS app_bank_transactions (
        user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        id TEXT NOT NULL, connection_id TEXT NOT NULL REFERENCES app_bank_connections(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL, date TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL,
        name TEXT, merchant TEXT, category TEXT, pending INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id,id))""",
    "CREATE INDEX IF NOT EXISTS app_bank_transactions_user_date ON app_bank_transactions(user_id,date)",
    """CREATE TABLE IF NOT EXISTS app_bank_sync_jobs (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        connection_id TEXT NOT NULL UNIQUE REFERENCES app_bank_connections(id) ON DELETE CASCADE,
        status TEXT NOT NULL, claim_token TEXT, rerun_requested INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0, next_run_at BIGINT NOT NULL,
        lease_until BIGINT, last_error TEXT, last_success_at BIGINT,
        created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS app_sync_jobs_due ON app_bank_sync_jobs(status,next_run_at)",
    """CREATE TABLE IF NOT EXISTS app_audit (
        id TEXT PRIMARY KEY, user_id TEXT, action TEXT NOT NULL, created_at BIGINT NOT NULL,
        detail TEXT)""",
]


# Additive, version-tracked migrations from Milestones 1 and 2, retaining rows.
# Review and rehearse staging/rollback for live deployments.
M2_COLUMNS = {
    "app_bank_connections": {"last_synced_at": "BIGINT"},
    "app_bank_transactions": {
        "pending_transaction_id": "TEXT", "currency": "TEXT", "amount_decimal": "TEXT",
    },
}
M3_COLUMNS = {
    "app_users": {
        "email_verified_at": "BIGINT", "mfa_secret": "TEXT", "mfa_pending": "TEXT",
        "mfa_pending_expires": "BIGINT",
    },
    "app_sessions": {"id": "TEXT"},
}
# Milestone 6: durable, secret-free worker heartbeat for operator monitoring.
M6_DDL = [
    """CREATE TABLE IF NOT EXISTS app_worker_heartbeat (
        service TEXT PRIMARY KEY, last_seen_at BIGINT NOT NULL)""",
]
# Milestone 7: supporting indexes for security cleanup and account-scoped
# transaction reconciliation. All are additive; existing records are retained.
M7_COLUMNS = {"app_sessions": {"created_at_ns": "BIGINT"}}
M7_DDL = [
    "CREATE INDEX IF NOT EXISTS app_sessions_expiry ON app_sessions(expires_at)",
    "CREATE INDEX IF NOT EXISTS app_auth_tokens_expiry ON app_auth_tokens(expires_at)",
    "CREATE INDEX IF NOT EXISTS app_mfa_challenges_expiry ON app_mfa_challenges(expires_at)",
    "CREATE INDEX IF NOT EXISTS app_login_attempts_start ON app_login_attempts(start_at)",
    "CREATE INDEX IF NOT EXISTS app_bank_transactions_connection ON app_bank_transactions(connection_id,account_id)",
]

M3_DDL = [
    """CREATE TABLE IF NOT EXISTS app_auth_tokens (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL, expires_at BIGINT NOT NULL, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS app_auth_tokens_user ON app_auth_tokens(user_id,purpose)",
    """CREATE TABLE IF NOT EXISTS app_mfa_challenges (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        attempts INTEGER NOT NULL DEFAULT 0, expires_at BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS app_mfa_recovery_codes (
        user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL, PRIMARY KEY (user_id,code_hash))""",
]


def _add_columns(c, pg, mapping):
    for table, columns in mapping.items():
        if pg:
            existing = {r["column_name"] for r in c.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s",
                (table,)).fetchall()}
        else:
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, dtype in columns.items():
            if name not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {dtype}")


def _migrate(c, pg):
    applied = {r["version"] for r in execute(c, "SELECT version FROM app_schema_migrations").fetchall()}
    if 1 not in applied:
        execute(c, "INSERT INTO app_schema_migrations(version,applied_at) VALUES (?,?)", (1, int(time.time())))
    if 2 not in applied:
        _add_columns(c, pg, M2_COLUMNS)
        execute(c, "INSERT INTO app_schema_migrations(version,applied_at) VALUES (?,?)", (2, int(time.time())))
    if 3 not in applied:
        for sql in M3_DDL:
            c.execute(sql)
        _add_columns(c, pg, M3_COLUMNS)
        # Retain sessions from prior milestones without exposing raw token hashes.
        rows = execute(c, "SELECT token_hash FROM app_sessions WHERE id IS NULL").fetchall()
        for row in rows:
            execute(c, "UPDATE app_sessions SET id=? WHERE token_hash=?",
                    (str(uuid.uuid4()), row["token_hash"]))
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS app_sessions_id ON app_sessions(id)")
        execute(c, "INSERT INTO app_schema_migrations(version,applied_at) VALUES (?,?)", (3, int(time.time())))
    if 4 not in applied:
        for statement in M6_DDL:
            c.execute(statement)
        execute(c, "INSERT INTO app_schema_migrations(version,applied_at) VALUES (?,?)", (4, int(time.time())))
    if 5 not in applied:
        _add_columns(c, pg, M7_COLUMNS)
        for statement in M7_DDL:
            c.execute(statement)
        execute(c, "INSERT INTO app_schema_migrations(version,applied_at) VALUES (?,?)", (5, int(time.time())))


def _settings():
    url = os.getenv("APP_DATABASE_URL", "")
    if url and not url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("APP_DATABASE_URL must be a PostgreSQL URL")
    path = Path(os.getenv("APP_DB_PATH", "data/ahead.sqlite3"))
    if not path.is_absolute():
        path = ROOT / path
    return url, path


def is_pg():
    return bool(_settings()[0])


def _open():
    url, path = _settings()
    if url:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(url, row_factory=dict_row, connect_timeout=5)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    if path.exists():
        os.chmod(path, 0o600)
    return conn


def execute(c, sql, params=()):
    if is_pg():
        sql = sql.replace("?", "%s")
    return c.execute(sql, params)


def initialize():
    key = tuple(map(str, _settings()))
    with INIT_LOCK:
        if key in INITIALIZED:
            return
        c = _open()
        try:
            pg = is_pg()
            if not pg:
                c.execute("PRAGMA journal_mode=WAL")
            else:
                # Serializes migrations across API and worker processes.
                c.execute("SELECT pg_advisory_xact_lock(636925031)")
            for sql in DDL:
                c.execute(sql)
            # Existing installations predate version tracking. Their tables and
            # records are kept; migration 3 is additive, and is repeatable.
            _migrate(c, pg)
            c.commit()
            INITIALIZED.add(key)
        finally:
            c.close()


@contextmanager
def connect():
    initialize()
    c = _open()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
