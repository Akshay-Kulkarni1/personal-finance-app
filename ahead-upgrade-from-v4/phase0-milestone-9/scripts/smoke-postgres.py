#!/usr/bin/env python3
"""Milestone 7: destructive-free PostgreSQL migration + multi-user HTTP smoke.

Requires a disposable PostgreSQL database named ahead_ci on loopback and
AHEAD_TEST_PG_URL. This script creates its OWN random schema, never modifies
public or any existing Ahead schema, and drops only that temporary schema.

    AHEAD_TEST_PG_URL=postgresql://ahead_test:test@127.0.0.1:5432/ahead_ci \
      python scripts/smoke-postgres.py

No Plaid account or credentials required; bank API calls are not made.
"""
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlsplit, unquote

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from urllib.parse import urlencode, urlunsplit, parse_qsl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def safe_test_url():
    url = os.environ.get("AHEAD_TEST_PG_URL", "")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("postgres", "postgresql") or
            parsed.hostname not in ("localhost", "127.0.0.1", "::1") or
            unquote(parsed.path) != "/ahead_ci"):
        raise RuntimeError("Refusing to run: AHEAD_TEST_PG_URL must point to a loopback PostgreSQL database named ahead_ci")
    if not parsed.username:
        raise RuntimeError("AHEAD_TEST_PG_URL must name a dedicated test database user")
    return url


def prepare_legacy_m2_schema(db_url):
    """Construct a previously installed M2 schema with an account and session."""
    import appdb
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        for statement in appdb.DDL:
            c.execute(statement)
        appdb._add_columns(c, True, appdb.M2_COLUMNS)
        c.execute("INSERT INTO app_schema_migrations(version,applied_at) VALUES (1,0),(2,0)")
        c.execute("INSERT INTO app_users(id,email,password_hash,created_at) VALUES (%s,%s,%s,%s)",
                  ("old-user", "existing@example.test", "legacy-hash", 1))
        c.execute("""INSERT INTO app_sessions(token_hash,user_id,csrf,expires_at,created_at)
                     VALUES (%s,%s,%s,%s,%s)""",
                  ("legacy-session-hash", "old-user", "old-csrf", int(time.time()) + 3000, 1))
    print("PASS: disposable Milestone 2 PostgreSQL schema prepared")


def acceptance():
    import appdb
    import main
    from cryptography.fernet import Fernet

    # Verify real appdb.initialize upgrade (not merely direct SQL migration calls).
    appdb.initialize()
    with appdb.connect() as c:
        versions = [r["version"] for r in appdb.execute(c, "SELECT version FROM app_schema_migrations ORDER BY version")]
        assert versions == [1, 2, 3, 4, 5], versions
        old = appdb.execute(c, "SELECT email,email_verified_at FROM app_users WHERE id=?", ("old-user",)).fetchone()
        assert old["email"] == "existing@example.test" and old["email_verified_at"] is None
        old_session = appdb.execute(c, "SELECT id FROM app_sessions WHERE user_id=?", ("old-user",)).fetchone()
        assert len(old_session["id"]) == 36
    print("PASS: additive migration preserved existing M2 user/session and generated session ID")

    def register(c, email):
        result = c.post("/auth/register", json={"email": email, "password": "PG-Only-Secure-Demo-Password_123"})
        assert result.status_code == 201, result.text
        c.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        return result.json()["user"]["id"]

    with TestClient(main.app) as alice, TestClient(main.app) as bob:
        a_id = register(alice, "alice.pg@example.test")
        b_id = register(bob, "bob.pg@example.test")
        assert a_id != b_id
        assert alice.get("/auth/me").json()["user"]["id"] == a_id
        assert bob.get("/auth/me").json()["user"]["id"] == b_id

        # Simulated provider rows test tenant isolation without Plaid credentials.
        now = int(time.time())
        with appdb.connect() as c:
            for uid, suffix in ((a_id, "a"), (b_id, "b")):
                appdb.execute(c, """INSERT INTO app_bank_connections
                    (id,user_id,item_id,encrypted_token,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    ("pg-con-" + suffix, uid, "pg-item-" + suffix,
                     Fernet(os.environ["AHEAD_ENCRYPTION_KEY"].encode()).encrypt(b"fake-unused-token").decode(),
                     "connected", now, now))
                appdb.execute(c, """INSERT INTO app_bank_accounts
                    (user_id,id,connection_id,name,updated_at) VALUES (?,?,?,?,?)""",
                    (uid, "pg-account-" + suffix, "pg-con-" + suffix, "Test account " + suffix, now))
                appdb.execute(c, """INSERT INTO app_bank_transactions
                    (user_id,id,connection_id,account_id,date,amount,name,pending,amount_decimal)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (uid, "pg-txn-" + suffix, "pg-con-" + suffix, "pg-account-" + suffix,
                     "2026-11-15", -22.5, "Test charge " + suffix, 0, "-22.50"))
        aa = alice.get("/bank/accounts")
        ba = bob.get("/bank/accounts")
        assert aa.status_code == ba.status_code == 200
        assert [x["id"] for x in aa.json()["accounts"]] == ["pg-account-a"]
        assert [x["id"] for x in ba.json()["accounts"]] == ["pg-account-b"]
        assert [x["id"] for x in alice.get("/bank/transactions").json()["transactions"]] == ["pg-txn-a"]
        assert [x["id"] for x in bob.get("/bank/transactions").json()["transactions"]] == ["pg-txn-b"]
        assert alice.get("/bank/connections").json()["connections"][0]["id"] == "pg-con-a"
        print("PASS: PostgreSQL-backed registration, cookies, financial data and two-user isolation")

        # Removing an account without linked banks is forbidden. No real external
        # bank tokens have been exchanged and these test records stay in test DB.
        denied = alice.post("/auth/privacy/delete", json={"password": "PG-Only-Secure-Demo-Password_123"})
        assert denied.status_code == 409, denied.text
        with appdb.connect() as c:
            appdb.execute(c, "DELETE FROM app_bank_connections WHERE id=? AND user_id=?", ("pg-con-a", a_id))
        deleted = alice.post("/auth/privacy/delete", json={"password": "PG-Only-Secure-Demo-Password_123"})
        assert deleted.status_code == 200 and deleted.json()["deleted"]
        assert alice.get("/auth/me").status_code == 401
        with appdb.connect() as c:
            assert not appdb.execute(c, "SELECT id FROM app_bank_accounts WHERE user_id=?", (a_id,)).fetchone()
            assert not appdb.execute(c, "SELECT id FROM app_bank_transactions WHERE user_id=?", (a_id,)).fetchone()
            assert appdb.execute(c, "SELECT id FROM app_users WHERE id=?", (b_id,)).fetchone()
        print("PASS: deletion cascades correctly in PostgreSQL and retains the other user's data")


def main():
    base = safe_test_url()
    import psycopg
    from psycopg import sql
    schema = "ahead_m7_" + uuid.uuid4().hex[:16]
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        parsed = urlsplit(base)
        query = urlencode([*parse_qsl(parsed.query), ("options", "-csearch_path=" + schema)])
        scoped = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
        os.environ["APP_DATABASE_URL"] = scoped
        os.environ["AHEAD_ENV"] = "development"
        os.environ["AHEAD_DEMO_MODE"] = "false"
        os.environ["AHEAD_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        os.environ.pop("SMTP_HOST", None)
        os.environ.pop("PLAID_CLIENT_ID", None)
        os.environ.pop("PLAID_SECRET", None)
        # Local SMTP fallback writes only to a temporary test outbox.
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix="ahead-m6-mail-") as outbox:
            os.environ["DEV_MAIL_OUTBOX"] = outbox
            prepare_legacy_m2_schema(scoped)
            acceptance()
    finally:
        # Clean up ONLY the script's random schema, not public or existing data.
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    print("PASS: PostgreSQL acceptance finished, temporary schema removed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: PostgreSQL smoke test: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
