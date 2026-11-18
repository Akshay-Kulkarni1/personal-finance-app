"""Phase 0 local acceptance: user-facing transaction signs and idempotent sync.

Uses fictional Plaid-shaped data and a disposable SQLite database; not a live
Plaid test or verification of real transaction classification.
"""
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bank
import main


@pytest.fixture
def sandbox_local(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "financial.sqlite3"))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("AHEAD_ENV", "development")
    monkeypatch.setenv("AHEAD_DEMO_MODE", "false")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_CLIENT_ID", "fixture-client-not-real")
    monkeypatch.setenv("PLAID_SECRET", "fixture-secret-not-real")
    monkeypatch.setenv("AHEAD_BANK_CONNECTIONS_ENABLED", "true")
    monkeypatch.setenv("AHEAD_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_provider_signs_survive_api_storage_and_repeated_sync(sandbox_local, monkeypatch):
    """Compare the raw provider sign with the exact API/DB decimal on both sides."""
    account_id = "fixture-checking"
    raw_transactions = [
        ("purchase", "Uber", "5.40", "-5.40"),
        ("payroll-deposit", "Payroll credit", "-5850.00", "5850.00"),
        ("refund", "Refund", "-0.01", "0.01"),
        # A name like 'credit' does not justify silently changing provider signs.
        ("ambiguous-credit", "ACH Electronic Credit", "5850.00", "-5850.00"),
        ("fractional", "Test merchant", "0.10", "-0.10"),
    ]
    seen_cursors = []

    def provider(endpoint, body):
        if endpoint == "/item/public_token/exchange":
            return {"access_token": "fake-test-token", "item_id": "fixture-item"}
        if endpoint == "/accounts/get":
            return {"accounts": [{
                "account_id": account_id, "name": "Test Checking", "type": "depository",
                "balances": {"current": 1200.25, "iso_currency_code": "USD"},
            }]}
        if endpoint == "/transactions/sync":
            seen_cursors.append(body.get("cursor"))
            if body.get("cursor") is None:
                return {
                    "added": [
                        {"transaction_id": key, "account_id": account_id,
                         "date": "2026-11-17", "amount": amount, "pending": False,
                         "name": name, "iso_currency_code": "USD"}
                        for key, name, amount, _expected in raw_transactions
                    ],
                    "modified": [], "removed": [], "next_cursor": "cursor-1", "has_more": False,
                }
            if body.get("cursor") == "cursor-1":
                return {"added": [], "modified": [], "removed": [],
                        "next_cursor": "cursor-2", "has_more": False}
            raise AssertionError("Unexpected cursor")
        raise AssertionError("Unexpected provider call: " + endpoint)

    monkeypatch.setattr(bank, "_call", provider)
    with TestClient(main.app) as client:
        registration = client.post("/auth/register", json={
            "email": "sign-check@example.test", "password": "a-strong-fake-test-password-1"
        })
        assert registration.status_code == 201, registration.text
        client.headers["X-CSRF-Token"] = registration.json()["csrf_token"]
        connected = client.post("/bank/exchange", json={"public_token": "fake-public-token"})
        assert connected.status_code == 201, connected.text
        cid = connected.json()["connection_id"]
        assert client.post("/bank/sync", json={"connection_id": cid}).status_code == 200
        rows = {tx["id"]: tx for tx in client.get("/bank/transactions").json()["transactions"]}
        assert len(rows) == len(raw_transactions)
        for key, name, raw_amount, expected in raw_transactions:
            assert rows[key]["amount_decimal"] == expected, (key, raw_amount, rows[key])
            assert rows[key]["name"] == name
            assert rows[key]["currency"] == "USD"
        assert client.post("/bank/sync", json={"connection_id": cid}).status_code == 200
        again = client.get("/bank/transactions").json()["transactions"]
        assert len(again) == len(raw_transactions)
        assert {t["id"]: t["amount_decimal"] for t in again} == {
            k: expected for k, _, _, expected in raw_transactions
        }
        assert seen_cursors == [None, "cursor-1"]


def test_existing_item_webhook_registration_and_sandbox_trigger_are_user_owned(sandbox_local, monkeypatch):
    """Owner may test a configured callback; no one can supply a callback URL."""
    events = []

    def provider(endpoint, body):
        events.append((endpoint, body))
        if endpoint == "/item/public_token/exchange":
            return {"access_token": "fake-owner-token", "item_id": "fixture-webhook-item"}
        if endpoint == "/accounts/get":
            return {"accounts": [{"account_id": "webhook-account", "name": "Checking",
                                  "type": "depository", "balances": {"current": 10}}]}
        if endpoint == "/item/webhook/update":
            assert body == {"access_token": "fake-owner-token", "webhook": "https://ahead.example.org/bank/webhook"}
            return {"item": {"item_id": "fixture-webhook-item"}}
        if endpoint == "/sandbox/item/fire_webhook":
            assert body == {"access_token": "fake-owner-token", "webhook_type": "TRANSACTIONS",
                            "webhook_code": "SYNC_UPDATES_AVAILABLE"}
            return {"webhook_fired": True}
        raise AssertionError(endpoint)

    monkeypatch.setattr(bank, "_call", provider)
    monkeypatch.setenv("PLAID_WEBHOOK_URL", "https://ahead.example.org/bank/webhook")
    with TestClient(main.app) as alice, TestClient(main.app) as bob:
        def register(client, email):
            r = client.post("/auth/register", json={"email": email, "password": "strong-test-only-password-12345"})
            assert r.status_code == 201
            client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        register(alice, "owner-webhook@example.test")
        register(bob, "someone-else@example.test")
        new = alice.post("/bank/exchange", json={"public_token": "fake-public-token"})
        assert new.status_code == 201
        cid = new.json()["connection_id"]
        assert bob.post("/bank/webhook/configure", json={"connection_id": cid}).status_code == 404
        assert bob.post("/bank/sandbox-fire-webhook", json={"connection_id": cid}).status_code == 404
        assert alice.post("/bank/webhook/configure", json={"connection_id": cid}).json() == {"configured": True}
        assert alice.post("/bank/sandbox-fire-webhook", json={"connection_id": cid}).json() == {"requested": True}
        assert [name for name, _ in events[-2:]] == ["/item/webhook/update", "/sandbox/item/fire_webhook"]
        monkeypatch.setenv("PLAID_ENV", "production")
        assert alice.post("/bank/sandbox-fire-webhook", json={"connection_id": cid}).status_code == 403


@pytest.mark.parametrize("bad_url", [
    "http://staging.example.org/bank/webhook",
    "https://localhost:8000/bank/webhook",
    "https://127.0.0.1/bank/webhook",
    "https://192.168.1.15/bank/webhook",
    "https://operator:password@example.org/bank/webhook",
    "https://staging.example.org/bank/webhook?secret=foo",
    "https://staging.example.org:8443/bank/webhook",
])
def test_invalid_callback_rejected_before_provider_call(sandbox_local, monkeypatch, bad_url):
    monkeypatch.setenv("PLAID_WEBHOOK_URL", bad_url)
    with pytest.raises(Exception) as ex:
        bank._webhook_target()
    assert getattr(ex.value, "status_code", None) == 503
