"""User-owned data access and account closure (Phase 0 / Milestone 5).

The export is intentionally read-only and excludes passwords, MFA secrets,
reset tokens, session cookies, Plaid access tokens, and worker credentials.
Account closure requires disconnecting all Plaid Items *first* so a local
cascade cannot accidentally leave an active financial-data authorization.

Backups and local-development email outboxes need separate retention handling.
"""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import appdb
import auth

router = APIRouter(prefix="/auth/privacy", tags=["privacy"])


class ConfirmIdentity(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=32)


def _confirm(c, user_id, confirmation: ConfirmIdentity):
    """Require recent possession of a password and MFA (if enabled).

    Attempts are recorded in the existing durable throttle table; invalid
    confirmations cannot be brute-forced without rate limiting.
    """
    key = "privacy:" + user_id
    if auth._too_many(c, key):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes")
    row = appdb.execute(c, "SELECT password_hash,mfa_secret FROM app_users WHERE id=?", (user_id,)).fetchone()
    valid = bool(row and auth._verify_password(row["password_hash"], confirmation.password))
    if valid and row["mfa_secret"]:
        valid = bool(confirmation.code and auth._mfa_check(c, user_id, row["mfa_secret"], confirmation.code))
    if not valid:
        auth._failed(c, key)
        c.commit()  # preserve throttle counters when raising HTTPException
        raise HTTPException(401, "Password or authenticator code is incorrect")
    appdb.execute(c, "DELETE FROM app_login_attempts WHERE key=?", (key,))


# Explicit allowlists make it difficult to accidentally add credential columns
# to the export when application tables evolve.
EXPORT_TABLES = {
    "bank_connections": """SELECT id,institution_id,institution_name,status,created_at,
        updated_at,last_synced_at FROM app_bank_connections WHERE user_id=? ORDER BY created_at,id""",
    "bank_accounts": """SELECT id,connection_id,name,mask,type,subtype,currency,
        current_balance,available_balance,updated_at FROM app_bank_accounts WHERE user_id=? ORDER BY id""",
    "bank_transactions": """SELECT id,connection_id,account_id,date,amount,amount_decimal,
        currency,name,merchant,category,pending,pending_transaction_id
        FROM app_bank_transactions WHERE user_id=? ORDER BY date,id""",
    "bank_sync_status": """SELECT connection_id,status,attempts,last_success_at
        FROM app_bank_sync_jobs WHERE user_id=? ORDER BY connection_id""",
    "audit_history": """SELECT action,created_at,detail FROM app_audit WHERE user_id=? ORDER BY created_at,id""",
}


def _export_stream(user_id, exported_at):
    """Stream the complete export without loading transaction history into RAM."""
    with appdb.connect() as c:
        user = appdb.execute(c, """SELECT id,email,created_at,email_verified_at
            FROM app_users WHERE id=?""", (user_id,)).fetchone()
        if user is None:
            # The requester deleted the account after the auth check.
            yield json.dumps({"error": "Account no longer exists"})
            return
        yield '{"format":"ahead-user-data-v1","exported_at":' + str(exported_at)
        yield ',"user":' + json.dumps(dict(user), ensure_ascii=False)
        for name, sql in EXPORT_TABLES.items():
            yield ',"' + name + '":['
            first = True
            for row in appdb.execute(c, sql, (user_id,)):
                if not first:
                    yield ','
                first = False
                yield json.dumps(dict(row), ensure_ascii=False, allow_nan=False)
            yield ']'
        yield '}'


@router.post("/export")
def export_personal_data(body: ConfirmIdentity, who=Depends(auth.require)):
    """Password/MFA-confirmed download, restricted to the current user."""
    with appdb.connect() as c:
        _confirm(c, who["id"], body)
    timestamp = int(time.time())
    return StreamingResponse(_export_stream(who["id"], timestamp),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="ahead-data-{timestamp}.json"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        })


@router.post("/delete")
def delete_account(body: ConfirmIdentity, response: Response, who=Depends(auth.require)):
    """Delete app-held account data only after the user disconnects every bank.

    Never delete a live Plaid access token locally before /item/remove succeeds.
    Backups/outbox copies are outside this database operation; see runbook.
    """
    with appdb.connect() as c:
        # There is no safe automatic provider unlink in a SQL transaction.
        # Force explicit bank disconnects and confirm they have disappeared.
        remaining = appdb.execute(c, "SELECT COUNT(*) AS n FROM app_bank_connections WHERE user_id=?", (who["id"],)).fetchone()["n"]
        if remaining:
            raise HTTPException(409, "Disconnect every bank in Banks before deleting your Ahead account")
        _confirm(c, who["id"], body)
        # The audit table intentionally has no FK; purge it explicitly.
        appdb.execute(c, "DELETE FROM app_audit WHERE user_id=?", (who["id"],))
        appdb.execute(c, "DELETE FROM app_login_attempts WHERE key=?",
                      ("login-account:" + auth._digest(who["email"]),))
        appdb.execute(c, "DELETE FROM app_users WHERE id=?", (who["id"],))
    response.delete_cookie(auth.COOKIE, httponly=True, secure=auth.os.getenv("AHEAD_ENV") == "production",
                           samesite="lax", path="/")
    return {"deleted": True}
