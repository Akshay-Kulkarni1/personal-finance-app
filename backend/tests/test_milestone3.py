"""Phase 0 Milestone 3: identity lifecycle, MFA, migrations, backup, security headers."""
import os
import re
import sqlite3
import sys
from pathlib import Path
from email import policy
from email.parser import BytesParser

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import appdb
import auth
import backup
import main

PASS = "VeryLongAndUniqueTestPassword_9"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "ahead.sqlite3"))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("AHEAD_ENV", "development")
    monkeypatch.setenv("AHEAD_DEMO_MODE", "false")
    monkeypatch.setenv("AHEAD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DEV_MAIL_OUTBOX", str(tmp_path / "mail"))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    yield tmp_path


def reg(client, email="testing@example.com"):
    r = client.post("/auth/register", json={"email": email, "password": PASS})
    assert r.status_code == 201, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.json()


def emailed_token(tmp_path):
    emails = sorted((tmp_path / "mail").glob("*.eml"), key=lambda p: p.stat().st_mtime_ns)
    assert emails
    msg = BytesParser(policy=policy.default).parsebytes(emails[-1].read_bytes())
    match = re.search(r"#token=([\w-]+)", msg.get_content())
    assert match
    return match.group(1)


def test_email_verification_and_password_reset_are_single_use(env):
    with TestClient(main.app) as c:
        account = reg(c)
        assert not account["user"]["email_verified"]
        assert account["csrf_token"]
        assert "token" not in account.get("user", {})
        assert c.get("/auth/me").json()["user"]["email_verified"] is False
        first = emailed_token(env)
        assert c.post("/auth/verify-email", json={"token": first}).status_code == 200
        assert c.get("/auth/me").json()["user"]["email_verified"] is True
        assert c.post("/auth/verify-email", json={"token": first}).status_code == 400

        assert c.post("/auth/forgot-password", json={"email": "unknown@example.com"}).status_code == 200
        reset = c.post("/auth/forgot-password", json={"email": "testing@example.com"})
        assert reset.status_code == 200 and "token" not in reset.text
        token = emailed_token(env)
        assert token != first
        assert c.post("/auth/reset-password", json={"token": token, "password": "ChangedPasswordMustBeLong_8"}).status_code == 200
        assert c.get("/auth/me").status_code == 401
        assert c.post("/auth/reset-password", json={"token": token, "password": PASS}).status_code == 400
        assert c.post("/auth/login", json={"email": "testing@example.com", "password": PASS}).status_code == 401
        assert c.post("/auth/login", json={"email": "testing@example.com", "password": "ChangedPasswordMustBeLong_8"}).status_code == 200


def test_mfa_challenge_recovery_and_session_revocation(env):
    with TestClient(main.app) as c:
        reg(c)
        assert c.post("/auth/mfa/setup", json={"password": "bad-password"*3}).status_code == 401
        setup = c.post("/auth/mfa/setup", json={"password": PASS})
        assert setup.status_code == 200
        secret = setup.json()["secret"]
        code = auth._totp(secret)
        enabled = c.post("/auth/mfa/enable", json={"code": code})
        assert enabled.status_code == 200
        rec = enabled.json()["recovery_codes"]
        assert len(rec) == 10 and len(set(rec)) == 10
        assert c.get("/auth/me").json()["user"]["mfa_enabled"] is True
        assert c.post("/auth/logout").status_code == 200
        attempt = c.post("/auth/login", json={"email":"testing@example.com", "password":PASS})
        assert attempt.json()["mfa_required"] and "csrf_token" not in attempt.json()
        challenge = attempt.json()["challenge"]
        assert c.get("/auth/me").status_code == 401
        assert c.post("/auth/mfa/complete", json={"challenge":challenge,"code":"0000000"}).status_code == 401
        verified = c.post("/auth/mfa/complete", json={"challenge":challenge,"code":code})
        assert verified.status_code == 200
        assert c.post("/auth/mfa/complete", json={"challenge":challenge,"code":code}).status_code == 401
        c.headers["X-CSRF-Token"] = verified.json()["csrf_token"]
        sessions = c.get("/auth/sessions").json()["sessions"]
        assert len(sessions) == 1 and sessions[0]["current"]
        assert c.post("/auth/sessions/revoke", json={"session_id":sessions[0]["id"]}).status_code == 200
        assert c.get("/auth/me").status_code == 401

        attempt2 = c.post("/auth/login", json={"email":"testing@example.com", "password":PASS})
        login_with_recovery = c.post("/auth/mfa/complete", json={"challenge":attempt2.json()["challenge"], "code":rec[0]})
        assert login_with_recovery.status_code == 200
        c.headers["X-CSRF-Token"] = login_with_recovery.json()["csrf_token"]
        assert c.post("/auth/mfa/disable", json={"password":PASS, "code":rec[0]}).status_code == 401
        assert c.post("/auth/mfa/disable", json={"password":PASS, "code":auth._totp(secret)}).status_code == 200
        assert c.get("/auth/me").json()["user"]["mfa_enabled"] is False


def test_failed_mfa_challenge_locks_after_five_attempts(env):
    with TestClient(main.app) as c:
        reg(c)
        secret = c.post("/auth/mfa/setup", json={"password":PASS}).json()["secret"]
        assert c.post("/auth/mfa/enable", json={"code":auth._totp(secret)}).status_code == 200
        assert c.post("/auth/logout").status_code == 200
        challenge = c.post("/auth/login", json={"email":"testing@example.com","password":PASS}).json()["challenge"]
        for _ in range(5):
            assert c.post("/auth/mfa/complete", json={"challenge":challenge,"code":"invalid-otp"}).status_code == 401
        assert c.post("/auth/mfa/complete", json={"challenge":challenge,"code":auth._totp(secret)}).status_code == 401


def test_database_migration_and_verified_backup(env):
    appdb.initialize()
    with appdb.connect() as c:
        assert [r["version"] for r in appdb.execute(c,"SELECT version FROM app_schema_migrations ORDER BY version")] == [1,2,3,4,5]
        c.execute("INSERT INTO app_users(id,email,password_hash,created_at) VALUES ('u1','u1@example.com','hash',0)")
    output = backup.backup(env / "backup" / "backup.sqlite3")
    assert output.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(output) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT email FROM app_users").fetchone()[0] == "u1@example.com"
    with pytest.raises(FileExistsError):
        backup.backup(output)


def test_security_headers_and_readiness(env):
    with TestClient(main.app) as c:
        r = c.get("/health")
        assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-request-id"]
        assert c.get("/ready").json() == {"ready": True}
        reg(c)
        r = c.get("/auth/me")
        assert r.headers["cache-control"] == "no-store"
        assert c.post("/auth/resend-verification").status_code == 200


def test_totp_rfc6238_known_vector():
    # RFC 6238 20-byte SHA-1 test secret: 94287082 at t=59 (6-digit suffix).
    assert auth._totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) == "287082"


def test_upgrade_from_previous_session_schema(env):
    """Existing accounts and cookies are retained when migrating an M2 database."""
    path = env / "ahead.sqlite3"
    with sqlite3.connect(path) as c:
        for sql in appdb.DDL:
            if "app_schema_migrations" not in sql:
                c.execute(sql)
        c.execute("INSERT INTO app_users VALUES ('existing','old@example.com','hash',100)")
        c.execute("INSERT INTO app_sessions VALUES ('old-token-hash','existing','csrf',9999999999,100)")
    appdb.initialize()
    with appdb.connect() as c:
        row = appdb.execute(c,"SELECT email,email_verified_at FROM app_users WHERE id='existing'").fetchone()
        assert row["email"] == "old@example.com" and row["email_verified_at"] is None
        session = appdb.execute(c,"SELECT id FROM app_sessions WHERE user_id='existing'").fetchone()
        assert session["id"] and len(session["id"]) == 36
        assert [r["version"] for r in appdb.execute(c,"SELECT version FROM app_schema_migrations ORDER BY version")] == [1,2,3,4,5]


def test_production_requires_verified_email_for_banks(env, monkeypatch):
    monkeypatch.setenv("AHEAD_ENV", "production")
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://app.example.org")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.org")
    # Capture delivery (without using a real SMTP service in a unit test).
    sent = []
    import mailer
    monkeypatch.setattr(mailer,"send_link",lambda recipient,purpose,token: sent.append(token))
    with TestClient(main.app, base_url="https://testserver") as c:
        assert c.get("/ready").status_code == 503  # missing real SMTP service
        reg(c)
        assert c.get("/bank/status").status_code == 403
        assert c.post("/auth/verify-email",json={"token":sent[0]}).status_code == 200
        assert c.get("/bank/status").status_code == 200
        assert c.get("/auth/me").json()["user"]["email_verified"] is True
