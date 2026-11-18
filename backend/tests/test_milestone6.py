"""M6 integration harness safety invariants; no external service required."""
import importlib.util
from pathlib import Path
import pytest

PROJECT = Path(__file__).resolve().parents[2]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_postgres_smoke_refuses_missing_and_production_targets(monkeypatch):
    pg = module("pg_smoke_test", "smoke-postgres.py")
    monkeypatch.delenv("AHEAD_TEST_PG_URL", raising=False)
    with pytest.raises(RuntimeError, match="loopback"):
        pg.safe_test_url()
    for target in [
        "postgresql://user:test@db.production.example.com/ahead_ci",
        "postgresql://user:test@localhost/main_database",
        "postgresql://user:test@127.0.0.1:5432/ahead",
        "sqlite:////tmp/ahead.sqlite3",
    ]:
        monkeypatch.setenv("AHEAD_TEST_PG_URL", target)
        with pytest.raises(RuntimeError, match="loopback"):
            pg.safe_test_url()
    monkeypatch.setenv("AHEAD_TEST_PG_URL", "postgresql://ahead_test:example@127.0.0.1:5432/ahead_ci")
    assert pg.safe_test_url().endswith("/ahead_ci")


def test_browser_smoke_does_not_visit_nonlocal_servers():
    browser = module("browser_smoke_test", "browser-smoke.py")
    for target in ["https://ahead.example.org", "http://bank.example.org:3000", "file:///etc/passwd"]:
        with pytest.raises(ValueError, match="local development"):
            browser.run(target)


def test_worker_heartbeat_and_operator_status(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(PROJECT / "backend"))
    import appdb
    import main
    import worker
    from fastapi.testclient import TestClient

    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "m6.sqlite3"))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("AHEAD_ENV", "development")
    monkeypatch.setenv("AHEAD_DEMO_MODE", "false")
    monkeypatch.setenv("AHEAD_OPS_TOKEN", "Q" * 40)
    appdb.initialize()
    with appdb.connect() as c:
        assert [r["version"] for r in appdb.execute(c, "SELECT version FROM app_schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5]
    with TestClient(main.app) as c:
        def status():
            return c.get("/ops/status", headers={"X-Ahead-Ops-Token": "Q" * 40}).json()
        assert status()["worker_last_seen_at"] is None
        assert status()["worker_observed_recently"] is False
        worker.heartbeat()
        value = status()
        assert isinstance(value["worker_last_seen_at"], int)
        assert value["worker_observed_recently"] is True
        # A stale heartbeat should not be advertised as a running worker.
        with appdb.connect() as db:
            appdb.execute(db, "UPDATE app_worker_heartbeat SET last_seen_at=1 WHERE service='bank-sync'")
        assert status()["worker_observed_recently"] is False


def test_upgrade_existing_milestone5_database_in_place(tmp_path, monkeypatch):
    """Version 3 user's rows survive the additive v4 heartbeat migration."""
    import sqlite3
    import sys
    sys.path.insert(0, str(PROJECT / "backend"))
    import appdb

    database = tmp_path / "existing-m5.sqlite3"
    monkeypatch.setenv("APP_DB_PATH", str(database))
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    with sqlite3.connect(database) as c:
        c.row_factory = sqlite3.Row
        for statement in appdb.DDL:
            c.execute(statement)
        appdb._add_columns(c, False, appdb.M2_COLUMNS)
        appdb._add_columns(c, False, appdb.M3_COLUMNS)
        for statement in appdb.M3_DDL:
            c.execute(statement)
        c.execute("INSERT INTO app_schema_migrations(version,applied_at) VALUES (1,0),(2,0),(3,0)")
        c.execute("INSERT INTO app_users (id,email,password_hash,created_at) VALUES ('m5-user','from-m5@example.test','hash',1)")
        c.execute("INSERT INTO app_sessions (token_hash,user_id,csrf,expires_at,created_at,id) VALUES ('hash','m5-user','token',9999999999,1,'original-session-id')")
    appdb.initialize()
    with appdb.connect() as c:
        assert [r["version"] for r in appdb.execute(c, "SELECT version FROM app_schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5]
        assert appdb.execute(c, "SELECT email FROM app_users WHERE id='m5-user'").fetchone()["email"] == "from-m5@example.test"
        assert appdb.execute(c, "SELECT id FROM app_sessions WHERE user_id='m5-user'").fetchone()["id"] == "original-session-id"
        assert appdb.execute(c, "SELECT COUNT(*) AS n FROM app_worker_heartbeat").fetchone()["n"] == 0
    # Running initialize again must not destroy an existing heartbeat or record.
    import worker
    worker.heartbeat()
    with appdb.connect() as c:
        assert appdb.execute(c, "SELECT COUNT(*) AS n FROM app_worker_heartbeat").fetchone()["n"] == 1
