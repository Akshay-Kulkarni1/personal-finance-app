"""Phase 0 regression for safe Git contents and browser assertion specificity."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _policy():
    source = ROOT / 'scripts' / 'check-repo-safety.py'
    spec = importlib.util.spec_from_file_location('ahead_repo_safety', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sensitive_paths_refused_but_templates_allowed():
    policy = _policy()
    assert policy.prohibited_paths([
        'backend/.env', 'backend/data/ahead.sqlite3',
        'frontend/node_modules/abc/index.js', '.venv/bin/python',
        'private.key', '.next/BUILD_ID',
    ]) == [
        'backend/.env', 'backend/data/ahead.sqlite3',
        'frontend/node_modules/abc/index.js', '.venv/bin/python',
        'private.key', '.next/BUILD_ID',
    ]
    assert policy.prohibited_paths([
        'backend/.env.example', 'frontend/package-lock.json',
        '.github/workflows/ci.yml', 'backend/schema.sql',
        'frontend/app/page.tsx',
    ]) == []


def test_browser_error_assertion_is_specific_to_app_message():
    code = (ROOT / 'scripts' / 'browser-smoke.py').read_text()
    assert 'get_by_text("Invalid email or password", exact=True)' in code
    assert 'get_by_role("alert")' not in code
