"""Milestone 2: durable sync, transaction integrity, upgrade, and signed webhooks."""
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import appdb
import bank
import main
import sync_jobs


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "ahead.sqlite3"))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("AHEAD_ENV", "development")
    monkeypatch.setenv("AHEAD_DEMO_MODE", "false")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_CLIENT_ID", "sandbox-client")
    monkeypatch.setenv("PLAID_SECRET", "sandbox-secret")
    monkeypatch.setenv("AHEAD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    yield


def register(c, email="customer@example.com"):
    r = c.post("/auth/register", json={"email": email, "password": "my-very-long-dev-password"})
    assert r.status_code == 201, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.json()["user"]["id"]


def linked(c, monkeypatch, provider):
    monkeypatch.setattr(bank, "_call", provider)
    response = c.post("/bank/exchange", json={"public_token": "test-public-token"})
    assert response.status_code == 201, response.text
    return response.json()["connection_id"]


def account(account_id="ac1"):
    return {"account_id": account_id, "name": "Checking", "type": "depository", "balances": {"current": 300, "iso_currency_code": "USD"}}


def test_queue_single_claim_and_webhook_coalescing(environment, monkeypatch):
    def provider(path, body):
        if path == "/item/public_token/exchange":
            return {"access_token": "token", "item_id": "item1"}
        if path == "/accounts/get":
            return {"accounts": [account()]}
        raise AssertionError(path)
    with TestClient(main.app) as c:
        register(c)
        cid = linked(c, monkeypatch, provider)
        first = sync_jobs.claim()
        assert first and first["connection_id"] == cid
        assert sync_jobs.claim() is None
        with appdb.connect() as db:
            sync_jobs.enqueue(db, first["user_id"], cid)
            row = appdb.execute(db, "SELECT status,rerun_requested FROM app_bank_sync_jobs WHERE connection_id=?", (cid,)).fetchone()
            assert row["status"] == "running" and row["rerun_requested"] == 1
        sync_jobs.finish(first["id"], first["claim_token"], success=True)
        second = sync_jobs.claim()
        assert second and second["claim_token"] != first["claim_token"]
        # An expired older worker cannot overwrite this worker's state.
        sync_jobs.finish(first["id"], first["claim_token"], success=False)
        with appdb.connect() as db:
            assert appdb.execute(db, "SELECT status FROM app_bank_sync_jobs WHERE id=?", (second["id"],)).fetchone()["status"] == "running"
        sync_jobs.finish(second["id"], second["claim_token"], success=True)
        assert sync_jobs.claim() is None


def test_failed_worker_retry_and_reconnect_status(environment, monkeypatch):
    def provider(path, body):
        if path == "/item/public_token/exchange":
            return {"access_token": "token", "item_id": "item2"}
        if path == "/accounts/get":
            return {"accounts": [account()]}
        if path == "/transactions/sync":
            raise HTTPException(409, "BANK_RELINK_REQUIRED")
        if path == "/link/token/create":
            assert "access_token" in body and "products" not in body
            return {"link_token": "update-link-token"}
        raise AssertionError(path)
    with TestClient(main.app) as c:
        register(c)
        cid = linked(c, monkeypatch, provider)
        r = c.post("/bank/sync", json={"connection_id": cid})
        assert r.status_code == 409
        conn = c.get("/bank/connections").json()["connections"][0]
        assert conn["status"] == "needs_attention"
        assert conn["sync_status"] == "needs_attention"
        assert c.post("/bank/update-link-token", json={"connection_id":cid}).json()["link_token"] == "update-link-token"
        assert c.post("/bank/update-complete", json={"connection_id":cid}).json() == {"queued": True}
        assert c.get("/bank/connections").json()["connections"][0]["status"] == "connected"


def test_pending_posted_conversion_exact_decimal_and_account_revocation(environment, monkeypatch):
    state = {"stage": 0}
    def provider(path, body):
        if path == "/item/public_token/exchange":
            return {"access_token": "token", "item_id": "item3"}
        if path == "/accounts/get":
            return {"accounts": [] if state["stage"] == 2 else [account()]}
        if path == "/transactions/sync":
            if state["stage"] == 0:
                return {"added": [{"transaction_id": "pending1", "account_id": "ac1", "date": "2026-11-15", "amount": 12.1, "pending": True, "name": "Store"}], "modified": [], "removed": [], "next_cursor": "first", "has_more": False}
            if state["stage"] == 1:
                assert body["cursor"] == "first"
                return {"added": [{"transaction_id": "posted1", "account_id": "ac1", "date": "2026-11-16", "amount": 12.34, "pending": False, "pending_transaction_id": "pending1", "name": "Store final"}], "modified": [], "removed": [], "next_cursor": "second", "has_more": False}
            return {"added": [], "modified": [], "removed": [], "next_cursor": "third", "has_more": False}
        raise AssertionError(path)
    with TestClient(main.app) as c:
        register(c)
        cid = linked(c, monkeypatch, provider)
        assert c.post("/bank/sync", json={"connection_id":cid}).status_code == 200
        assert c.get("/bank/transactions").json()["transactions"][0]["id"] == "pending1"
        state["stage"] = 1
        assert c.post("/bank/sync", json={"connection_id":cid}).status_code == 200
        rows = c.get("/bank/transactions").json()["transactions"]
        assert len(rows) == 1 and rows[0]["id"] == "posted1"
        assert rows[0]["pending_transaction_id"] == "pending1"
        assert rows[0]["amount_decimal"] == "-12.34" and rows[0]["currency"] == "USD"
        state["stage"] = 2
        assert c.post("/bank/sync", json={"connection_id":cid}).status_code == 200
        assert c.get("/bank/accounts").json()["accounts"] == []
        assert c.get("/bank/transactions").json()["transactions"] == []


def test_invalid_money_rollback_keeps_cursor(environment, monkeypatch):
    def provider(path, body):
        if path == "/item/public_token/exchange":
            return {"access_token": "token", "item_id": "item4"}
        if path == "/accounts/get":
            return {"accounts": [account()]}
        if path == "/transactions/sync":
            return {"added": [{"transaction_id": "bad", "account_id": "ac1", "date": "2026-11-15", "amount": "NaN", "pending": False}], "modified": [], "removed": [], "next_cursor": "do-not-save", "has_more": False}
        raise AssertionError(path)
    with TestClient(main.app) as c:
        register(c)
        cid = linked(c, monkeypatch, provider)
        assert c.post("/bank/sync", json={"connection_id":cid}).status_code == 502
        with appdb.connect() as db:
            conn = appdb.execute(db, "SELECT cursor FROM app_bank_connections WHERE id=?", (cid,)).fetchone()
            assert conn["cursor"] is None
        assert c.get("/bank/transactions").json()["transactions"] == []


def test_additive_migration_on_old_installation(environment):
    # Represent a Milestone 1 schema without newly added columns.
    path = Path(appdb._settings()[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE app_bank_connections(id TEXT PRIMARY KEY,user_id TEXT,item_id TEXT,encrypted_token TEXT,institution_id TEXT,institution_name TEXT,cursor TEXT,status TEXT,created_at BIGINT,updated_at BIGINT)")
        conn.execute("CREATE TABLE app_bank_transactions(user_id TEXT,id TEXT,connection_id TEXT,account_id TEXT,date TEXT,amount DOUBLE PRECISION,name TEXT,merchant TEXT,category TEXT,pending INTEGER,PRIMARY KEY(user_id,id))")
    with appdb.connect() as db:
        columns = {r["name"] for r in db.execute("PRAGMA table_info(app_bank_transactions)").fetchall()}
        assert {"amount_decimal", "currency", "pending_transaction_id"}.issubset(columns)
        assert {r["name"] for r in db.execute("PRAGMA table_info(app_bank_connections)").fetchall()} >= {"last_synced_at"}
        assert db.execute("SELECT COUNT(*) FROM app_bank_sync_jobs").fetchone()[0] == 0


def test_signed_webhook_requires_jwt_body_integrity_and_known_item(environment, monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    from jwt.utils import base64url_encode
    def b64(n):
        return base64url_encode(n.to_bytes(32, "big")).decode()
    jwk = {"kid": "testkid", "kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "x": b64(numbers.x), "y": b64(numbers.y)}
    def provider(path, body):
        if path == "/item/public_token/exchange":
            return {"access_token": "token", "item_id": "item5"}
        if path == "/accounts/get":
            return {"accounts": [account()]}
        if path == "/webhook_verification_key/get":
            assert body["key_id"] == "testkid"
            return {"key": jwk}
        raise AssertionError(path)
    def signed(body, age=0):
        claims = {"iat": int(time.time()) - age, "request_body_sha256": hashlib.sha256(body).hexdigest()}
        return jwt.encode(claims, private, algorithm="ES256", headers={"kid": "testkid"})
    with TestClient(main.app) as c:
        register(c)
        cid = linked(c, monkeypatch, provider)
        payload = json.dumps({"item_id": "item5", "webhook_type": "TRANSACTIONS", "webhook_code": "SYNC_UPDATES_AVAILABLE"}).encode()
        assert c.post("/bank/webhook", content=payload).status_code == 401
        assert c.post("/bank/webhook", content=payload+b" ", headers={"Plaid-Verification": signed(payload)}).status_code == 401
        assert c.post("/bank/webhook", content=payload, headers={"Plaid-Verification": signed(payload, age=600)}).status_code == 401
        ok = c.post("/bank/webhook", content=payload, headers={"Plaid-Verification": signed(payload)})
        assert ok.status_code == 200, ok.text
        with appdb.connect() as db:
            assert db.execute("SELECT status FROM app_bank_sync_jobs WHERE connection_id=?", (cid,)).fetchone()["status"] == "queued"
        # Well-signed unrelated Item doesn't disclose whether one exists.
        unknown = json.dumps({"item_id": "unknown-item", "webhook_type": "TRANSACTIONS", "webhook_code": "SYNC_UPDATES_AVAILABLE"}).encode()
        assert c.post("/bank/webhook", content=unknown, headers={"Plaid-Verification": signed(unknown)}).json() == {"received": True}


def test_webhook_origin_independent_but_untrusted_sync_denied(environment, monkeypatch):
    # A public callback is not a user-auth route, but it must not accept unsigned commands.
    with TestClient(main.app) as c:
        assert c.post("/bank/webhook", json={"item_id": "anything", "webhook_type": "TRANSACTIONS", "webhook_code": "SYNC_UPDATES_AVAILABLE"}).status_code == 401
        assert c.post("/bank/sync", json={"connection_id": "other"}).status_code == 401


def test_stale_sweep_respects_retry_backoff(environment, monkeypatch):
    def provider(path, body):
        if path == "/item/public_token/exchange":
            return {"access_token": "token", "item_id": "item6"}
        if path == "/accounts/get":
            return {"accounts": [account()]}
        raise AssertionError(path)
    with TestClient(main.app) as c:
        register(c)
        cid = linked(c, monkeypatch, provider)
        job = sync_jobs.claim()
        assert job
        sync_jobs.finish(job["id"], job["claim_token"], success=False)
        assert sync_jobs.claim() is None
        assert sync_jobs.schedule_stale() == 0
        with appdb.connect() as db:
            row = appdb.execute(db, "SELECT status,next_run_at FROM app_bank_sync_jobs WHERE connection_id=?", (cid,)).fetchone()
            assert row["status"] == "retry" and row["next_run_at"] > time.time()


def test_provider_pagination_mutation_replays_original_cursor(environment, monkeypatch):
    calls = []
    def provider(path, body):
        if path == "/item/public_token/exchange":
            return {"access_token": "token", "item_id": "item7"}
        if path == "/accounts/get":
            return {"accounts": [account()]}
        if path == "/transactions/sync":
            cursor = body["cursor"]
            calls.append(cursor)
            if cursor is None:
                return {"added": [{"transaction_id":"first", "account_id":"ac1", "date":"2026-11-15", "amount":5}], "modified":[],"removed":[],"next_cursor":"page1","has_more":True}
            if cursor == "page1" and len(calls) == 2:
                raise HTTPException(409, "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION")
            if cursor == "page1":
                return {"added": [{"transaction_id":"second", "account_id":"ac1", "date":"2026-11-16", "amount":6}], "modified":[],"removed":[],"next_cursor":"final","has_more":False}
        raise AssertionError((path, body))
    with TestClient(main.app) as c:
        register(c)
        cid = linked(c, monkeypatch, provider)
        r = c.post("/bank/sync", json={"connection_id":cid})
        assert r.status_code == 200, r.text
        assert calls == [None, "page1", None, "page1"]
        assert {t["id"] for t in c.get("/bank/transactions").json()["transactions"]} == {"first", "second"}
        with appdb.connect() as db:
            assert appdb.execute(db, "SELECT cursor FROM app_bank_connections WHERE id=?", (cid,)).fetchone()["cursor"] == "final"
