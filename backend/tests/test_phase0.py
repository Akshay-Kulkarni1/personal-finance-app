"""Phase 0: security and user-owned Plaid lifecycle, without external credentials."""
import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import appdb
import bank
import main


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("AHEAD_ENV", "development")
    monkeypatch.setenv("AHEAD_DEMO_MODE", "false")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_CLIENT_ID", "client-test")
    monkeypatch.setenv("PLAID_SECRET", "secret-test")
    monkeypatch.setenv("AHEAD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    yield


def register(client, email):
    r = client.post("/auth/register", json={"email": email, "password": "AnExcellentLongPassword_123"})
    assert r.status_code == 201, r.text
    data = r.json()
    assert r.cookies["ahead_session"]
    assert "HttpOnly" in r.headers["set-cookie"]
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    return data


def test_authentication_and_protected_demo(environment):
    with TestClient(main.app) as c:
        assert c.get("/health").json() == {"ok": True}
        assert c.get("/summary").status_code == 401
        assert c.post("/approve", json={"action": {"kind": "transfer"}}).status_code == 401
        reg = register(c, "First@example.com")
        assert reg["user"]["email"] == "first@example.com"
        assert c.get("/auth/me").json()["user"]["id"] == reg["user"]["id"]
        assert c.get("/summary").status_code == 503  # no seeded Maya data for real users
        assert c.post("/auth/register", json={"email":"First@example.com","password":"AnExcellentLongPassword_123"}).status_code == 409
        c.headers.pop("X-CSRF-Token")
        assert c.post("/bank/link-token").status_code == 403
        assert c.post("/auth/logout").status_code == 403
        c.headers["X-CSRF-Token"] = reg["csrf_token"]
        assert c.post("/auth/logout").status_code == 200
        assert c.get("/auth/me").status_code == 401
        assert c.post("/auth/login", json={"email": "first@example.com", "password": "incorrect-password-123"}).status_code == 401
        logged_in = c.post("/auth/login", json={"email": "first@example.com", "password": "AnExcellentLongPassword_123"})
        assert logged_in.status_code == 200
        assert logged_in.json()["user"]["id"] == reg["user"]["id"]


def test_bank_link_sync_disconnect_and_user_isolation(environment, monkeypatch):
    calls = []
    def provider(path, body):
        calls.append((path, body))
        if path == "/link/token/create":
            return {"link_token":"link-sandbox-abc"}
        if path == "/item/public_token/exchange":
            return {"access_token": "secret-bank-token-should-not-leak", "item_id":"item-1"}
        if path == "/accounts/get":
            return {"accounts": [{"account_id": "a1", "name": "My Checking", "mask": "1111", "type":"depository", "subtype":"checking", "balances": {"current": 500.50, "available": 480.50, "iso_currency_code":"USD"}}]}
        if path == "/transactions/sync":
            if body["cursor"]:
                return {"added":[], "modified":[], "removed":[], "next_cursor":"page-2", "has_more":False}
            return {"added": [{"transaction_id": "t1", "account_id": "a1", "amount":25.5,
                                "date": "2026-11-15", "name":"Cafe purchase", "merchant_name":"Cafe", "pending":False,
                                "personal_finance_category": {"primary":"FOOD_AND_DRINK"}}],
                    "modified":[],"removed":[], "next_cursor":"page-1", "has_more":False}
        if path == "/item/remove":
            return {"removed": True}
        raise AssertionError(path)
    monkeypatch.setattr(bank, "_call", provider)

    with TestClient(main.app) as alice, TestClient(main.app) as bob:
        first = register(alice, "alice@example.com")
        second = register(bob, "bob@example.com")
        assert first["user"]["id"] != second["user"]["id"]
        assert alice.post("/bank/link-token").json()["link_token"] == "link-sandbox-abc"
        exchanged = alice.post("/bank/exchange", json={"public_token":"public-1234", "institution_name":"Test Bank"})
        assert exchanged.status_code == 201
        cid = exchanged.json()["connection_id"]
        assert alice.get("/bank/connections").json()["connections"][0]["institution_name"] == "Test Bank"
        assert len(alice.get("/bank/accounts").json()["accounts"]) == 1
        assert bob.get("/bank/connections").json() == {"connections": []}
        assert bob.get("/bank/accounts").json() == {"accounts": []}
        assert bob.get("/bank/transactions").json() == {"transactions": []}
        assert bob.post("/bank/sync", json={"connection_id":cid}).status_code == 404
        assert bob.post("/bank/disconnect", json={"connection_id":cid}).status_code == 404
        assert alice.post("/bank/sync", json={"connection_id":cid}).json()["added"] == 1
        assert alice.post("/bank/sync", json={"connection_id":cid}).json()["added"] == 0
        trans = alice.get("/bank/transactions").json()["transactions"]
        assert len(trans) == 1 and trans[0]["amount"] == -25.5
        with appdb.connect() as db:
            row = appdb.execute(db, "SELECT encrypted_token FROM app_bank_connections WHERE id=?", (cid,)).fetchone()
            assert "secret-bank-token-should-not-leak" not in row["encrypted_token"]
        assert "secret-bank-token-should-not-leak" not in str(alice.get("/bank/connections").json())
        assert alice.post("/bank/disconnect", json={"connection_id":cid}).json()["ok"]
        assert alice.get("/bank/accounts").json() == {"accounts": []}
        assert alice.get("/bank/transactions").json() == {"transactions": []}
        assert alice.post("/bank/sync", json={"connection_id":cid}).status_code == 404
        assert any(path == "/item/remove" for path, _ in calls)


def test_bank_not_configured(environment, monkeypatch):
    monkeypatch.delenv("PLAID_SECRET")
    with TestClient(main.app) as c:
        register(c, "no-keys@example.com")
        assert not c.get("/bank/status").json()["configured"]
        assert c.post("/bank/link-token").status_code == 503


def test_reject_cross_origin_write(environment):
    with TestClient(main.app) as c:
        assert c.post("/auth/register", json={"email":"evil@example.com", "password":"AnExcellentLongPassword_123"},
                      headers={"Origin":"https://attacker.example"}).status_code == 403
        assert c.get("/auth/me").status_code == 401


def test_failed_sync_does_not_save_partial_changes(environment, monkeypatch):
    def provider(path, body):
        if path == "/item/public_token/exchange":
            return {"access_token": "bank-secret", "item_id": "rollback-item"}
        if path == "/accounts/get":
            return {"accounts": [{"account_id":"a9", "name":"Savings", "balances":{"current":120}}]}
        if path == "/transactions/sync":
            raise HTTPException(502, "Provider sync failed")
        raise AssertionError(path)
    from fastapi import HTTPException
    monkeypatch.setattr(bank, "_call", provider)
    with TestClient(main.app) as c:
        register(c, "rollback@example.com")
        resp = c.post("/bank/exchange", json={"public_token":"public-token-123"})
        assert resp.status_code == 201
        cid = resp.json()["connection_id"]
        assert c.post("/bank/sync", json={"connection_id":cid}).status_code == 502
        assert c.get("/bank/transactions").json() == {"transactions": []}
        with appdb.connect() as db:
            row = appdb.execute(db, "SELECT cursor FROM app_bank_connections WHERE id=?", (cid,)).fetchone()
            assert row["cursor"] is None
