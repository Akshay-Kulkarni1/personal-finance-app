"""Milestone 8 offline verification: bank-disabled development and release gates.

These are regression checks, not substitutes for live browser, Plaid, staging,
or third-party security assessments.
"""
import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import appdb
import main
import production_check


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'm8.sqlite3'))
    monkeypatch.delenv('APP_DATABASE_URL', raising=False)
    monkeypatch.setenv('AHEAD_ENV', 'development')
    monkeypatch.setenv('AHEAD_DEMO_MODE', 'false')
    monkeypatch.setenv('AHEAD_BANK_CONNECTIONS_ENABLED', 'false')
    monkeypatch.setenv('AHEAD_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('DEV_MAIL_OUTBOX', str(tmp_path / 'mail'))
    monkeypatch.delenv('SMTP_HOST', raising=False)
    monkeypatch.delenv('AHEAD_OPS_TOKEN', raising=False)
    return tmp_path


def test_postgresql_production_rejects_insecure_or_loopback_endpoints():
    cases = {
        'postgresql://user:pass@127.0.0.1:5432/ahead?sslmode=require': False,
        'postgresql://user:pass@localhost:5432/ahead?sslmode=verify-full': False,
        'postgresql://user:pass@db.internal/ahead?sslmode=disable': False,
        'postgresql://user:pass@db.internal/ahead?sslmode=prefer': False,
        'postgresql://user:pass@db.internal/ahead': False,
        'postgresql://user:pass@db.internal/ahead?sslmode=require': True,
        'postgresql://user:pass@db.internal/ahead?sslmode=verify-full': True,
        'postgres://user:pass@db.internal/ahead?sslmode=verify-ca': True,
        'postgresql://user:pass@db.internal/?sslmode=verify-full': False,
        'postgresql://user:pass@db.internal/ahead?sslmode=require&sslmode=disable': False,
    }
    for url, expected in cases.items():
        assert production_check._postgres_production_url(url) is expected, url


def test_safe_credential_free_staging_configuration_passes():
    env = {
        'AHEAD_ENV': 'production', 'AHEAD_DEMO_MODE': 'false',
        'APP_DATABASE_URL': 'postgresql://user:password@db.internal/ahead?sslmode=verify-full',
        'AHEAD_ENCRYPTION_KEY': Fernet.generate_key().decode(),
        'PUBLIC_APP_URL': 'https://ahead.example.org',
        'FRONTEND_ORIGINS': 'https://ahead.example.org',
        'SMTP_HOST': 'smtp.example.org', 'SMTP_USERNAME': 'sender', 'SMTP_PASSWORD': 'secret',
        'MAIL_FROM': 'Ahead <hello@ahead.example.org>',
        'AHEAD_OPS_TOKEN': 'p' * 48,
        'AHEAD_BANK_CONNECTIONS_ENABLED': 'false',
        'PLAID_ENV': 'production',
    }
    assert production_check.configuration_issues(env) == []
    assert any('APP_DATABASE_URL' in s for s in production_check.configuration_issues(
        env | {'APP_DATABASE_URL': 'postgresql://user:pass@localhost/ahead?sslmode=require'}))


def test_new_user_bank_disabled_mode_rejects_link_without_seeding_data(isolated):
    with TestClient(main.app) as c:
        email = 'm8@example.test'
        password = 'AheadPhaseZeroSecurePassword_2026'
        register = c.post('/auth/register', json={'email': email, 'password': password})
        assert register.status_code == 201, register.text
        c.headers['X-CSRF-Token'] = register.json()['csrf_token']
        assert c.get('/bank/accounts').json() == {'accounts': []}
        assert c.get('/bank/transactions').json() == {'transactions': []}
        assert c.get('/bank/status').json()['configured'] is False
        blocked = c.post('/bank/link-token')
        assert blocked.status_code == 503
        assert 'isn\'t configured' in blocked.json()['detail']
        assert c.get('/summary').status_code == 503
        assert c.post('/auth/privacy/delete', json={'password': password}).status_code == 200
        assert c.get('/auth/me').status_code == 401


def test_auth_protected_endpoints_and_safe_headers(isolated):
    with TestClient(main.app) as c:
        for path in ('/auth/me', '/bank/accounts', '/bank/transactions', '/auth/sessions'):
            assert c.get(path).status_code == 401
        assert c.get('/ops/status').status_code == 404
        r = c.get('/health')
        assert r.status_code == 200
        assert r.headers.get('x-frame-options') == 'DENY'
        assert r.headers.get('x-content-type-options') == 'nosniff'
        assert c.get('/ready').json() == {'ready': True}


def test_readiness_fails_closed_on_invalid_production_config(isolated, monkeypatch):
    monkeypatch.setenv('AHEAD_ENV', 'production')
    with TestClient(main.app) as c:
        r = c.get('/ready')
        assert r.status_code == 503 and r.json() == {'ready': False}
        assert c.get('/openapi.json').status_code in (401, 404)
