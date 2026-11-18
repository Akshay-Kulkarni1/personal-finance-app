"""Milestone 7: persistent security throttles, bounded retry queue, migration and recovery."""
import importlib.util
import sys
import time
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
import appdb
import auth
import bank
import main
import maintenance
import sync_jobs

PASSWORD = 'Phase7-test-password-secure_987'


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'm7.sqlite3'))
    monkeypatch.delenv('APP_DATABASE_URL', raising=False)
    monkeypatch.setenv('AHEAD_ENV', 'development')
    monkeypatch.setenv('AHEAD_DEMO_MODE', 'false')
    monkeypatch.setenv('AHEAD_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('DEV_MAIL_OUTBOX', str(tmp_path / 'outbox'))
    monkeypatch.delenv('SMTP_HOST', raising=False)
    return tmp_path


def register(client, email='m7@example.test'):
    response = client.post('/auth/register', json={'email': email, 'password': PASSWORD})
    assert response.status_code == 201, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return response.json()['user']['id']


def connection(uid):
    cid = str(uuid.uuid4())
    with appdb.connect() as c:
        appdb.execute(c, '''INSERT INTO app_bank_connections
            (id,user_id,item_id,encrypted_token,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)''', (cid, uid, 'test-item-'+cid, 'sealed-placeholder', 'connected', 1, 1))
        sync_jobs.enqueue(c, uid, cid)
    return cid


def test_account_wide_login_throttle_and_window_reset(isolated):
    with TestClient(main.app) as client:
        register(client)
        # These attempts could originate at 8 unrelated addresses; the shared
        # email hash is intentionally independent of request.client.host.
        account_key = 'login-account:' + auth._digest('m7@example.test')
        with appdb.connect() as c:
            for _ in range(8):
                auth._failed(c, account_key)
            assert auth._too_many(c, account_key)
        blocked = client.post('/auth/login', json={'email':'m7@example.test','password':PASSWORD})
        assert blocked.status_code == 429
        with appdb.connect() as c:
            appdb.execute(c, 'UPDATE app_login_attempts SET start_at=? WHERE key=?',
                          (int(time.time()) - 901, account_key))
        accepted = client.post('/auth/login', json={'email':'m7@example.test','password':PASSWORD})
        assert accepted.status_code == 200, accepted.text
        with appdb.connect() as c:
            assert not appdb.execute(c, 'SELECT key FROM app_login_attempts WHERE key=?', (account_key,)).fetchone()


def test_throttle_upsert_preserves_counts_then_resets(isolated):
    appdb.initialize()
    with appdb.connect() as c:
        for _ in range(3):
            auth._failed(c, 'login:test')
        count = appdb.execute(c, 'SELECT count FROM app_login_attempts WHERE key=?', ('login:test',)).fetchone()['count']
        assert count == 3
        appdb.execute(c, 'UPDATE app_login_attempts SET start_at=? WHERE key=?', (int(time.time())-901,'login:test'))
        auth._failed(c, 'login:test')
        assert appdb.execute(c, 'SELECT count FROM app_login_attempts WHERE key=?', ('login:test',)).fetchone()['count'] == 1


def test_session_limit_keeps_new_session_and_revokes_old(isolated):
    from fastapi import Response
    with TestClient(main.app) as client:
        uid = register(client)
        with appdb.connect() as c:
            earliest = appdb.execute(c, 'SELECT token_hash FROM app_sessions WHERE user_id=?', (uid,)).fetchone()['token_hash']
            for _ in range(17):
                auth._make_session(c, uid, Response())
            sessions = appdb.execute(c,'SELECT token_hash FROM app_sessions WHERE user_id=?',(uid,)).fetchall()
            assert len(sessions) == auth.MAX_SESSIONS_PER_USER
            assert earliest not in {r['token_hash'] for r in sessions}
            assert not appdb.execute(c,'SELECT token_hash FROM app_sessions WHERE token_hash=?',(earliest,)).fetchone()


def test_sync_retries_stop_and_manual_retry_restarts(isolated):
    with TestClient(main.app) as client:
        uid = register(client)
    cid = connection(uid)
    for attempt in range(sync_jobs.MAX_ATTEMPTS):
        job = sync_jobs.claim(connection_id=cid)
        assert job, attempt
        sync_jobs.finish(job['id'],job['claim_token'], success=False)
        with appdb.connect() as c:
            row = appdb.execute(c,'SELECT status,attempts FROM app_bank_sync_jobs WHERE connection_id=?',(cid,)).fetchone()
            assert row['attempts'] == attempt + 1
            if attempt < sync_jobs.MAX_ATTEMPTS - 1:
                assert row['status'] == 'retry'
                appdb.execute(c,'UPDATE app_bank_sync_jobs SET next_run_at=0 WHERE connection_id=?',(cid,))
    with appdb.connect() as c:
        row = appdb.execute(c,'SELECT status,last_error FROM app_bank_sync_jobs WHERE connection_id=?',(cid,)).fetchone()
        assert row['status'] == 'dead' and 'retry' in row['last_error']
        sync_jobs.enqueue(c,uid,cid)
        assert appdb.execute(c,'SELECT status FROM app_bank_sync_jobs WHERE connection_id=?',(cid,)).fetchone()['status']=='dead'
    assert sync_jobs.claim(connection_id=cid) is None
    with appdb.connect() as c:
        sync_jobs.enqueue(c,uid,cid,manual=True)
        row = appdb.execute(c,'SELECT status,attempts,last_error FROM app_bank_sync_jobs WHERE connection_id=?',(cid,)).fetchone()
        assert row['status']=='queued' and row['attempts']==0 and row['last_error'] is None
    new_job = sync_jobs.claim(connection_id=cid)
    assert new_job and new_job['claim_token'] != job['claim_token']
    sync_jobs.finish(new_job['id'],new_job['claim_token'],success=True)
    with appdb.connect() as c:
        assert appdb.execute(c,'SELECT status FROM app_bank_sync_jobs WHERE connection_id=?',(cid,)).fetchone()['status']=='done'


def test_automatic_events_do_not_bypass_reconnection(isolated):
    with TestClient(main.app) as client:
        uid=register(client)
    cid=connection(uid)
    with appdb.connect() as c:
        appdb.execute(c,"UPDATE app_bank_sync_jobs SET status='needs_attention' WHERE connection_id=?", (cid,))
        sync_jobs.enqueue(c,uid,cid)
        assert appdb.execute(c,'SELECT status FROM app_bank_sync_jobs WHERE connection_id=?',(cid,)).fetchone()['status']=='needs_attention'
        sync_jobs.enqueue(c,uid,cid,manual=True)
        assert appdb.execute(c,'SELECT status FROM app_bank_sync_jobs WHERE connection_id=?',(cid,)).fetchone()['status']=='queued'


def test_manual_sync_requires_relink_when_provider_revoked(isolated):
    with TestClient(main.app) as client:
        uid=register(client)
    cid=connection(uid)
    with appdb.connect() as c:
        appdb.execute(c,"UPDATE app_bank_connections SET status='needs_attention' WHERE id=?",(cid,))
    with pytest.raises(HTTPException,match='BANK_RELINK_REQUIRED'):
        sync_jobs.run_manual(uid,cid)
    with appdb.connect() as c:
        assert appdb.execute(c,'SELECT status FROM app_bank_sync_jobs WHERE connection_id=?',(cid,)).fetchone()['status']=='queued'


def test_cursor_conflict_is_atomic_and_does_not_write_transactions(isolated, monkeypatch):
    with TestClient(main.app) as client:
        uid=register(client)
    cid=connection(uid)
    with appdb.connect() as c:
        appdb.execute(c,"UPDATE app_bank_connections SET cursor='old' WHERE id=?",(cid,))
    def provider(endpoint, data):
        if endpoint == '/accounts/get':
            return {'accounts':[{'account_id':'checking','name':'Checking','balances':{'current':100}}]}
        if endpoint == '/transactions/sync':
            assert data['cursor']=='old'
            # Simulate an overlapping worker committing a later cursor.
            with appdb.connect() as c:
                appdb.execute(c,"UPDATE app_bank_connections SET cursor='new' WHERE id=?",(cid,))
            return {'added':[{'transaction_id':'txn','account_id':'checking',
                              'date':'2026-11-15','amount':20,'pending':False}],
                    'modified':[],'removed':[],'next_cursor':'outdated','has_more':False}
        raise AssertionError(endpoint)
    monkeypatch.setattr(bank,'_call',provider)
    monkeypatch.setattr(bank,'_get_owned_connection',lambda c,u,id: (appdb.execute(c,'SELECT * FROM app_bank_connections WHERE id=?',(id,)).fetchone(),'test-token'))
    with pytest.raises(HTTPException,match='cursor changed'):
        bank.sync_connection(uid,cid)
    with appdb.connect() as c:
        assert appdb.execute(c,'SELECT cursor FROM app_bank_connections WHERE id=?',(cid,)).fetchone()['cursor']=='new'
        assert not appdb.execute(c,'SELECT id FROM app_bank_transactions WHERE user_id=?',(uid,)).fetchall()


def test_security_retention_only_cleans_expired_records(isolated):
    with TestClient(main.app) as client:
        uid=register(client)
    now=int(time.time())
    with appdb.connect() as c:
        appdb.execute(c,'UPDATE app_sessions SET expires_at=? WHERE user_id=?',(now-1,uid))
        appdb.execute(c,'''INSERT INTO app_mfa_challenges(token_hash,user_id,attempts,expires_at)
            VALUES (?,?,0,?)''',('expired-challenge',uid,now-1))
        appdb.execute(c,'INSERT INTO app_login_attempts VALUES (?,?,?)',('expired-throttle',4,now-1000))
        appdb.execute(c,'INSERT INTO app_login_attempts VALUES (?,?,?)',('active-throttle',1,now))
        appdb.execute(c,'UPDATE app_auth_tokens SET expires_at=? WHERE user_id=?',(now-1,uid))
    stats=maintenance.prune_expired(now=now)
    assert stats['sessions']==1 and stats['auth_links']==1 and stats['mfa_challenges']==1
    assert stats['login_attempts']==1
    with appdb.connect() as c:
        assert appdb.execute(c,'SELECT id FROM app_users WHERE id=?',(uid,)).fetchone()
        assert appdb.execute(c,'SELECT key FROM app_login_attempts WHERE key=?',('active-throttle',)).fetchone()


def test_operator_check_is_safe_and_flags_backlog():
    spec=importlib.util.spec_from_file_location('ahead_ops_script', PROJECT/'scripts/check-ops.py')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for value in ['http://example.org/ops/status','http://example.org:80/ops/status',
                  'https://user:secret@example.org/ops/status',
                  'https://example.org/ops/status?token=secret']:
        with pytest.raises(ValueError):
            mod.safe_url(value)
    assert mod.safe_url('http://127.0.0.1:8000/ops/status')
    assert mod.safe_url('https://ops.example.org/ops/status')
    good={'database':'ok','worker_observed_recently':True,'overdue_jobs':0,'dead_letter_jobs':0,'sync_jobs':{}}
    assert mod.assess(good)==[]
    assert len(mod.assess(good | {'dead_letter_jobs':1,'overdue_jobs':3,'worker_observed_recently':False}))==3


def test_m7_migration_adds_indexes_and_preserves_existing_data(isolated):
    import sqlite3
    db=Path(__import__('os').environ['APP_DB_PATH'])
    with sqlite3.connect(db) as c:
        c.row_factory=sqlite3.Row
        for statement in appdb.DDL: c.execute(statement)
        for sql in appdb.M3_DDL: c.execute(sql)
        for sql in appdb.M6_DDL: c.execute(sql)
        appdb._add_columns(c,False,appdb.M2_COLUMNS)
        appdb._add_columns(c,False,appdb.M3_COLUMNS)
        c.executemany('INSERT INTO app_schema_migrations VALUES (?,0)',[(i,) for i in range(1,5)])
        c.execute("INSERT INTO app_users(id,email,password_hash,created_at) VALUES ('keep','keep@example.test','unused',1)")
    appdb.initialize()
    with appdb.connect() as c:
        assert [r['version'] for r in appdb.execute(c,'SELECT version FROM app_schema_migrations ORDER BY version')]==[1,2,3,4,5]
        assert appdb.execute(c,"SELECT email FROM app_users WHERE id='keep'").fetchone()['email']=='keep@example.test'
        indexes={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert 'app_auth_tokens_expiry' in indexes
    appdb.initialize()
    with appdb.connect() as c:
        assert appdb.execute(c,'SELECT COUNT(*) AS n FROM app_schema_migrations').fetchone()['n']==5


def test_credential_free_staging_requires_explicit_disabled_banking(isolated, monkeypatch):
    import production_check
    production_env = {
        'AHEAD_ENV':'production','AHEAD_DEMO_MODE':'false',
        'APP_DATABASE_URL':'postgresql://app:pass@db.internal/ahead?sslmode=verify-full',
        'AHEAD_ENCRYPTION_KEY':Fernet.generate_key().decode(),
        'PUBLIC_APP_URL':'https://staging.ahead.example.org',
        'FRONTEND_ORIGINS':'https://staging.ahead.example.org',
        'SMTP_HOST':'smtp.example.org','MAIL_FROM':'Ahead <hello@ahead.org>',
        'PLAID_ENV':'production','AHEAD_OPS_TOKEN':'x'*40,
        'AHEAD_BANK_CONNECTIONS_ENABLED':'false',
    }
    assert production_check.configuration_issues(production_env)==[]
    assert any('PLAID_CLIENT_ID' in e for e in production_check.configuration_issues(
        production_env | {'AHEAD_BANK_CONNECTIONS_ENABLED':'true'}))
    monkeypatch.setenv('AHEAD_BANK_CONNECTIONS_ENABLED','false')
    monkeypatch.setenv('PLAID_CLIENT_ID','fake-client-id')
    monkeypatch.setenv('PLAID_SECRET','fake-secret')
    assert bank.configured() is False
