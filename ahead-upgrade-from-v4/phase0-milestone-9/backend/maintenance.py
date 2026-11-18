"""Bounded retention for expired security artifacts, not financial records.

Called by the bank worker on startup and roughly hourly. This intentionally
never deletes financial transactions, audit history, user accounts or provider
permissions: those require separately reviewed retention/deletion policies.
"""
import time
import appdb


def prune_expired(*, now=None):
    now = int(time.time()) if now is None else int(now)
    with appdb.connect() as c:
        counts = {}
        queries = {
            "sessions": ("DELETE FROM app_sessions WHERE expires_at<=?", (now,)),
            "auth_links": ("DELETE FROM app_auth_tokens WHERE expires_at<=?", (now,)),
            "mfa_challenges": ("DELETE FROM app_mfa_challenges WHERE expires_at<=?", (now,)),
            "login_attempts": ("DELETE FROM app_login_attempts WHERE start_at<?", (now - 900,)),
        }
        for name, (sql, params) in queries.items():
            counts[name] = appdb.execute(c, sql, params).rowcount
        appdb.execute(c, """UPDATE app_users SET mfa_pending=NULL,mfa_pending_expires=NULL
            WHERE mfa_pending_expires IS NOT NULL AND mfa_pending_expires<=?""", (now,))
    return counts
