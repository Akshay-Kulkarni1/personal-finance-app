"""Phase 0 M5: privacy operations, isolation, cautious closure and deployment checks."""
import sys
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import appdb
import auth
import bank
import main
import production_check

PASSWORD = "SensitiveDataTestPassword_987"


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "privacy.sqlite3"))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("AHEAD_ENV", "development")
    monkeypatch.setenv("AHEAD_DEMO_MODE", "false")
    monkeypatch.setenv("AHEAD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DEV_MAIL_OUTBOX", str(tmp_path / "mail"))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("PLAID_CLIENT_ID", "sandbox-client")
    monkeypatch.setenv("PLAID_SECRET", "sandbox-secret")
    return tmp_path


def register(client, email):
    response = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()["user"]["id"]


def privacy_post(client, action, password=PASSWORD, code=None):
    return client.post("/auth/privacy/" + action, json={"password": password, "code": code})


def test_export_requires_auth_confirmation_and_csrf(environment):
    with TestClient(main.app) as client:
        assert privacy_post(client, "export").status_code == 401
        register(client, "alice@example.test")
        client.headers.pop("X-CSRF-Token")
        assert privacy_post(client, "export").status_code == 403
        client.headers["X-CSRF-Token"] = client.get("/auth/me").json()["csrf_token"]
        assert privacy_post(client, "export", "not-my-password").status_code == 401
        exported = privacy_post(client, "export")
        assert exported.status_code == 200
        assert "no-store" in exported.headers["cache-control"]
        assert exported.headers["content-disposition"].startswith("attachment; filename=")
        data = exported.json()
        assert data["format"] == "ahead-user-data-v1"
        assert data["user"]["email"] == "alice@example.test"
        assert data["bank_accounts"] == []
        assert data["bank_transactions"] == []
        assert all("password_hash" not in key for key in data["user"])
        assert PASSWORD not in exported.text
        assert "mfa_secret" not in exported.text
        assert "token_hash" not in exported.text


def test_privacy_export_scoped_to_requester_and_bank_disconnect_required(environment, monkeypatch):
    with TestClient(main.app) as alice, TestClient(main.app) as bob:
        aid = register(alice, "alice@example.test")
        bid = register(bob, "bob@example.test")
        now = int(time.time())
        with appdb.connect() as c:
            for uid, label in ((aid, "alice"), (bid, "bob")):
                appdb.execute(c, """INSERT INTO app_bank_connections
                    (id,user_id,item_id,encrypted_token,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    ("con-" + label, uid, "item-" + label, Fernet(auth.os.environ["AHEAD_ENCRYPTION_KEY"].encode()).encrypt(("encrypted-secret-" + label).encode()).decode(), "connected", now, now))
                appdb.execute(c, """INSERT INTO app_bank_accounts
                    (user_id,id,connection_id,name,updated_at) VALUES (?,?,?,?,?)""",
                    (uid, "account-" + label, "con-" + label, "Bank " + label, now))
                appdb.execute(c, """INSERT INTO app_bank_transactions
                    (user_id,id,connection_id,account_id,date,amount,name,pending,amount_decimal)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (uid, "txn-" + label, "con-" + label,
                    "account-" + label, "2026-11-15", -23.50, "Shop " + label, 0, "-23.50"))
                appdb.execute(c, "INSERT INTO app_audit VALUES (?,?,?,?,?)",
                              ("audit-" + label, uid, "bank.connected", now, "con-" + label))
        exported = privacy_post(alice, "export")
        assert exported.status_code == 200
        data = exported.json()
        assert data["bank_accounts"][0]["name"] == "Bank alice"
        assert data["bank_transactions"][0]["amount_decimal"] == "-23.50"
        assert data["audit_history"][0]["action"] == "bank.connected"
        assert "bob@example.test" not in exported.text
        assert "Shop bob" not in exported.text
        assert "encrypted-secret-alice" not in exported.text
        assert "encrypted-secret-bob" not in exported.text
        assert privacy_post(alice, "delete").status_code == 409
        # Bank disconnect must contact the provider BEFORE the record is removed.
        calls = []
        monkeypatch.setattr(bank, "_call", lambda endpoint, payload: calls.append(endpoint) or {"removed": True})
        disconnect = alice.post("/bank/disconnect", json={"connection_id": "con-alice"})
        assert disconnect.status_code == 200
        assert calls == ["/item/remove"]
        result = privacy_post(alice, "delete")
        assert result.status_code == 200 and result.json() == {"deleted": True}
        assert alice.get("/auth/me").status_code == 401
        assert privacy_post(alice, "export").status_code == 401
        with appdb.connect() as c:
            assert appdb.execute(c, "SELECT COUNT(*) AS n FROM app_users WHERE id=?", (aid,)).fetchone()["n"] == 0
            assert appdb.execute(c, "SELECT COUNT(*) AS n FROM app_audit WHERE user_id=?", (aid,)).fetchone()["n"] == 0
            assert appdb.execute(c, "SELECT COUNT(*) AS n FROM app_bank_transactions WHERE user_id=?", (aid,)).fetchone()["n"] == 0
            assert appdb.execute(c, "SELECT COUNT(*) AS n FROM app_users WHERE id=?", (bid,)).fetchone()["n"] == 1
        assert privacy_post(bob, "export").json()["bank_transactions"][0]["name"] == "Shop bob"


def test_wrong_password_cannot_delete_and_throttles(environment):
    with TestClient(main.app) as client:
        user_id = register(client, "nobody@example.test")
        for _ in range(8):
            assert privacy_post(client, "delete", "wrong-password").status_code == 401
        assert privacy_post(client, "delete", PASSWORD).status_code == 429
        with appdb.connect() as c:
            assert appdb.execute(c, "SELECT id FROM app_users WHERE id=?", (user_id,)).fetchone() is not None


def test_mfa_required_for_sensitive_download_and_deletion(environment):
    with TestClient(main.app) as client:
        uid = register(client, "mfa@example.test")
        raw = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        sealed = Fernet(auth.os.environ["AHEAD_ENCRYPTION_KEY"].encode()).encrypt(raw.encode()).decode()
        with appdb.connect() as c:
            appdb.execute(c, "UPDATE app_users SET mfa_secret=? WHERE id=?", (sealed, uid))
        assert privacy_post(client, "export").status_code == 401
        assert privacy_post(client, "export", code="000000").status_code == 401
        actual = auth._totp(raw)
        assert privacy_post(client, "export", code=actual).status_code == 200
        assert privacy_post(client, "delete").status_code == 401
        assert privacy_post(client, "delete", code=actual).json() == {"deleted": True}


def test_production_configuration_blocks_missing_or_unsafe_settings():
    assert production_check.configuration_issues({})
    valid = {
        "AHEAD_ENV": "production", "AHEAD_DEMO_MODE": "false",
        "APP_DATABASE_URL": "postgresql://user:password@db.internal:5432/ahead?sslmode=verify-full",
        "AHEAD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "PUBLIC_APP_URL": "https://ahead.example.org",
        "FRONTEND_ORIGINS": "https://ahead.example.org",
        "SMTP_HOST": "smtp.mail.example.org", "SMTP_USERNAME": "sender", "SMTP_PASSWORD": "secret",
        "MAIL_FROM": "Ahead <hello@myapp.org>",
        "PLAID_ENV": "production", "PLAID_CLIENT_ID": "id", "PLAID_SECRET": "secret",
        "AHEAD_OPS_TOKEN": "a" * 40,
    }
    assert production_check.configuration_issues(valid) == []
    assert any("FRONTEND_ORIGINS" in issue for issue in production_check.configuration_issues(valid | {
        "FRONTEND_ORIGINS": "https://ahead.example.org,https://evil.example.net/path"}))
    assert any("AHEAD_ENCRYPTION_KEY" in issue for issue in production_check.configuration_issues(valid | {
        "AHEAD_ENCRYPTION_KEY": "not-a-key"}))
    assert any("PLAID_ENV" in issue for issue in production_check.configuration_issues(valid | {
        "PLAID_ENV": "sandbox"}))
