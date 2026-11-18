"""Phase 0 M4: protected ops diagnostics, local origins and recovery drill."""
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import appdb
import main
from backup import backup
from restore_drill import restore_drill


@pytest.fixture
def local_env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("AHEAD_ENV", "development")
    monkeypatch.setenv("AHEAD_DEMO_MODE", "false")
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.delenv("AHEAD_OPS_TOKEN", raising=False)
    yield


def test_operator_status_is_protected_and_contains_no_user_data(local_env, monkeypatch):
    with TestClient(main.app) as c:
        assert c.get("/ops/status").status_code == 404
        assert c.get("/ops/status", headers={"X-Ahead-Ops-Token": "short"}).status_code == 404
        monkeypatch.setenv("AHEAD_OPS_TOKEN", "O" * 40)
        assert c.get("/ops/status", headers={"X-Ahead-Ops-Token": "X" * 40}).status_code == 404
        good = c.get("/ops/status", headers={"X-Ahead-Ops-Token": "O" * 40})
        assert good.status_code == 200
        assert good.json() == {"database": "ok", "sync_jobs": {}, "dead_letter_jobs": 0, "overdue_jobs": 0,
                               "last_success_at": None, "worker_last_seen_at": None,
                               "worker_observed_recently": False}
        assert "no-store" in good.headers["Cache-Control"]
        assert "X-Ahead-Ops-Token" not in str(good.json())


def test_operator_sees_aggregate_retry_backlog_without_identity(local_env, monkeypatch):
    monkeypatch.setenv("AHEAD_OPS_TOKEN", "a" * 40)
    now = int(time.time())
    with appdb.connect() as c:
        appdb.execute(c, "INSERT INTO app_users(id,email,password_hash,created_at) VALUES (?,?,?,?)",
                      ("user-private", "private@example.com", "hashed-secret", now))
        appdb.execute(c, """INSERT INTO app_bank_connections
            (id,user_id,item_id,encrypted_token,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)""", ("connection", "user-private", "item", "encrypted-sensitive", "connected", now, now))
        appdb.execute(c, """INSERT INTO app_bank_sync_jobs
            (id,user_id,connection_id,status,next_run_at,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)""", ("job", "user-private", "connection", "retry", now-400, now, now))
    with TestClient(main.app) as c:
        r = c.get("/ops/status", headers={"X-Ahead-Ops-Token": "a" * 40})
        assert r.status_code == 200
        assert r.json()["sync_jobs"]["retry"] == 1
        assert r.json()["overdue_jobs"] == 1
        assert "private@example.com" not in r.text
        assert "encrypted-sensitive" not in r.text


def test_dev_accepts_both_localhost_and_ipv4_loopback(local_env):
    with TestClient(main.app) as c:
        for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
            r = c.post("/auth/register", headers={"Origin": origin},
                json={"email": str(uuid.uuid4())+"@example.com", "password": "StrongDemoPassword_123"})
            assert r.status_code == 201, r.text
            assert r.headers.get("access-control-allow-origin") == origin
        blocked = c.post("/auth/register", headers={"Origin": "https://malicious.example"},
            json={"email": "denied@example.com", "password": "StrongDemoPassword_123"})
        assert blocked.status_code == 403


def test_backup_can_be_restored_in_isolated_database(local_env, tmp_path):
    with appdb.connect() as c:
        appdb.execute(c, "INSERT INTO app_users(id,email,password_hash,created_at) VALUES (?,?,?,?)",
                      ("user1", "my-email@example.com", "some-hash", 123))
    original = Path(os.environ["APP_DB_PATH"])
    snapshot = backup(tmp_path / "snapshot.sqlite3")
    assert restore_drill(snapshot)
    assert restore_drill()  # also exercises a fresh consistent backup
    # The drill doesn't touch the live file or its records.
    with sqlite3.connect(original) as c:
        assert c.execute("SELECT email FROM app_users").fetchone()[0] == "my-email@example.com"
    assert os.environ["APP_DB_PATH"] == str(original)


def test_bad_backup_fails_restore_drill_without_overwriting_live(local_env, tmp_path):
    damaged = tmp_path / "damaged.sqlite3"
    damaged.write_bytes(b"not-a-sqlite-database")
    with pytest.raises((sqlite3.DatabaseError, AssertionError)):
        restore_drill(damaged)
    assert os.environ["APP_DB_PATH"] == str(tmp_path / "app.sqlite3")
