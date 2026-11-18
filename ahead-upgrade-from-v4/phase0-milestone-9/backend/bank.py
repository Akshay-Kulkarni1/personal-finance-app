"""Plaid Link connection lifecycle. Read-only account and transaction access.

Plaid credentials and encrypted access tokens stay server-side. No bank credentials
are ever received by Ahead. Sandbox Link works with test institutions, no personal
bank account required.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import jwt

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
import httpx
from pydantic import BaseModel, Field

import appdb
import sync_jobs
from auth import require

router = APIRouter(prefix="/bank", tags=["bank connections"])
HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class Exchange(BaseModel):
    public_token: str = Field(min_length=5, max_length=512)
    institution_id: str | None = Field(default=None, max_length=120)
    institution_name: str | None = Field(default=None, max_length=200)


class ConnectionId(BaseModel):
    connection_id: str


def _credentials_available():
    return bool(os.getenv("PLAID_CLIENT_ID") and os.getenv("PLAID_SECRET") and os.getenv("AHEAD_ENCRYPTION_KEY"))


def configured():
    # Explicitly disable new linking/synchronization in a credential-free
    # staging environment. Provider revocation still works, if credentials
    # remain configured, so existing users can disconnect accounts.
    enabled = os.getenv("AHEAD_BANK_CONNECTIONS_ENABLED", "true").lower() == "true"
    return enabled and _credentials_available()


def _webhook_target():
    """Get a public HTTPS callback from server configuration, never user input.

    Existing linked Plaid Items need /item/webhook/update; adding a URL to
    PLAID_WEBHOOK_URL alone affects only newly created Link tokens.
    """
    raw = os.getenv("PLAID_WEBHOOK_URL", "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme != "https" or not parsed.netloc or not hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                hostname in ("localhost", "0.0.0.0") or
                hostname.endswith((".localhost", ".local", ".internal"))):
            raise ValueError("Not a public HTTPS URL")
        if parsed.port not in (None, 443):
            raise ValueError("Not an HTTPS default port")
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            ip = None  # Ordinary DNS hostname; reachability requires live testing.
        if ip is not None and not ip.is_global:
            raise ValueError("Not a public IP address")
    except ValueError as exc:
        raise HTTPException(503, "PLAID_WEBHOOK_URL must be a public HTTPS callback URL") from exc
    return raw


def _key():
    value = os.getenv("AHEAD_ENCRYPTION_KEY", "")
    try:
        return Fernet(value.encode())
    except (ValueError, TypeError) as e:
        raise HTTPException(503, "Bank token encryption is not configured") from e


def _call(endpoint, body):
    if not configured() and not (endpoint == "/item/remove" and _credentials_available()):
        raise HTTPException(503, "Bank connection isn't configured yet. Add Plaid Sandbox keys and an encryption key.")
    env = os.getenv("PLAID_ENV", "sandbox").lower()
    if env not in HOSTS:
        raise HTTPException(503, "Invalid PLAID_ENV setting")
    try:
        r = httpx.post(HOSTS[env] + endpoint, json=body,
                       headers={"PLAID-CLIENT-ID": os.environ["PLAID_CLIENT_ID"],
                                "PLAID-SECRET": os.environ["PLAID_SECRET"]}, timeout=25)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        # Do not expose provider response or credentials to the browser.
        code = e.response.status_code
        try:
            provider_code = e.response.json().get("error_code")
        except ValueError:
            provider_code = None
        if provider_code in ("ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "USER_PERMISSION_REVOKED"):
            raise HTTPException(409, "BANK_RELINK_REQUIRED") from e
        if provider_code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
            raise HTTPException(409, "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION") from e
        raise HTTPException(502, f"Bank provider could not complete the request ({code})") from e
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, "Bank provider is temporarily unavailable") from e


@router.get("/status")
def status(who=Depends(require)):
    return {"configured": configured(), "environment": os.getenv("PLAID_ENV", "sandbox"),
            "webhook_configured": bool(os.getenv("PLAID_WEBHOOK_URL", "").strip())}


@router.post("/link-token")
def link_token(who=Depends(require)):
    opts = {
        "client_name": "Ahead", "language": "en", "country_codes": ["US"],
        "products": ["transactions"], "user": {"client_user_id": who["id"]},
    }
    webhook_url = _webhook_target()
    if webhook_url:
        opts["webhook"] = webhook_url
    data = _call("/link/token/create", opts)
    return {"link_token": data["link_token"], "expiration": data.get("expiration")}


@router.post("/update-link-token")
def update_link_token(body: ConnectionId, who=Depends(require)):
    """Update mode repairs bank authentication; no new public-token exchange needed."""
    with appdb.connect() as c:
        _, token = _get_owned_connection(c, who["id"], body.connection_id)
    data = _call("/link/token/create", {
        "client_name": "Ahead", "language": "en", "country_codes": ["US"],
        "user": {"client_user_id": who["id"]}, "access_token": token,
    })
    return {"link_token": data["link_token"]}


@router.post("/update-complete")
def update_complete(body: ConnectionId, who=Depends(require)):
    """After Link update-mode onSuccess, queue a fresh sync."""
    with appdb.connect() as c:
        row = appdb.execute(c, "SELECT id FROM app_bank_connections WHERE id=? AND user_id=?", (body.connection_id, who["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Connection not found")
        appdb.execute(c, "UPDATE app_bank_connections SET status='connected',updated_at=? WHERE id=? AND user_id=?",
                      (int(time.time()), body.connection_id, who["id"]))
        sync_jobs.enqueue(c, who["id"], body.connection_id, manual=True)
    return {"queued": True}


def _save_accounts(c, user_id, connection_id, accounts):
    now = int(time.time())
    for a in accounts:
        bal = a.get("balances") or {}
        appdb.execute(c, """INSERT INTO app_bank_accounts
            (user_id,id,connection_id,name,mask,type,subtype,currency,current_balance,available_balance,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,id) DO UPDATE SET
            name=excluded.name, mask=excluded.mask, type=excluded.type,
            subtype=excluded.subtype, currency=excluded.currency,
            current_balance=excluded.current_balance, available_balance=excluded.available_balance,
            updated_at=excluded.updated_at""",
            (user_id, a["account_id"], connection_id, a.get("name", "Account"), a.get("mask"),
             a.get("type"), a.get("subtype"), bal.get("iso_currency_code") or bal.get("unofficial_currency_code"),
             bal.get("current"), bal.get("available"), now))


@router.post("/exchange", status_code=201)
def exchange(body: Exchange, who=Depends(require)):
    # A Plaid public token is issued only after an end-user completes Plaid Link.
    item = _call("/item/public_token/exchange", {"public_token": body.public_token})
    token = item["access_token"]
    item_id = item["item_id"]
    accounts = _call("/accounts/get", {"access_token": token}).get("accounts", [])
    encrypted = _key().encrypt(token.encode()).decode()
    now, cid = int(time.time()), str(uuid.uuid4())
    with appdb.connect() as c:
        exists = appdb.execute(c, "SELECT user_id FROM app_bank_connections WHERE item_id=?", (item_id,)).fetchone()
        if exists:
            raise HTTPException(409, "This bank connection already exists")
        appdb.execute(c, """INSERT INTO app_bank_connections
            (id,user_id,item_id,encrypted_token,institution_id,institution_name,cursor,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cid, who["id"], item_id, encrypted, body.institution_id, body.institution_name, None, "connected", now, now))
        _save_accounts(c, who["id"], cid, accounts)
        appdb.execute(c, "INSERT INTO app_audit VALUES (?,?,?,?,?)", (str(uuid.uuid4()),who["id"],"bank.connected",now,cid))
        sync_jobs.enqueue(c, who["id"], cid)
    return {"connection_id": cid, "accounts_connected": len(accounts)}


@router.get("/connections")
def connections(who=Depends(require)):
    with appdb.connect() as c:
        rows = appdb.execute(c, """SELECT b.id,b.institution_id,b.institution_name,b.status,
            b.created_at,b.updated_at,b.last_synced_at, j.status AS sync_status,
            j.next_run_at AS next_sync_at, j.last_error AS sync_error
            FROM app_bank_connections b LEFT JOIN app_bank_sync_jobs j ON j.connection_id=b.id
            WHERE b.user_id=? ORDER BY b.created_at DESC""", (who["id"],)).fetchall()
    return {"connections": [dict(r) for r in rows]}


@router.get("/accounts")
def accounts(who=Depends(require)):
    with appdb.connect() as c:
        rows = appdb.execute(c, """SELECT id,connection_id,name,mask,type,subtype,currency,
              current_balance,available_balance,updated_at
              FROM app_bank_accounts WHERE user_id=? ORDER BY name""", (who["id"],)).fetchall()
    return {"accounts": [dict(r) for r in rows]}


@router.get("/transactions")
def transactions(who=Depends(require)):
    with appdb.connect() as c:
        rows = appdb.execute(c, """SELECT id,account_id,date,amount,amount_decimal,currency,
              pending_transaction_id,name,merchant,category,pending
            FROM app_bank_transactions WHERE user_id=? ORDER BY date DESC,id DESC LIMIT 200""", (who["id"],)).fetchall()
    return {"transactions": [dict(r) for r in rows]}


def _get_owned_connection(c, uid, cid):
    row = appdb.execute(c, "SELECT * FROM app_bank_connections WHERE id=? AND user_id=?", (cid, uid)).fetchone()
    if not row:
        raise HTTPException(404, "Connection not found")
    try:
        token = _key().decrypt(row["encrypted_token"].encode()).decode()
    except InvalidToken as e:
        raise HTTPException(503, "Bank token could not be decrypted") from e
    return row, token


def _signed_decimal(amount):
    """Plaid expenses are positive; Ahead inflows positive. Exact decimal is canonical."""
    try:
        val = -Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as e:
        raise HTTPException(502, "Bank provider sent an invalid amount") from e
    if not val.is_finite():
        raise HTTPException(502, "Bank provider sent an invalid amount")
    return format(val, "f")


def sync_connection(user_id, connection_id):
    """Apply *all* sync pages, account reconciliation and cursor in one DB commit."""
    with appdb.connect() as c:
        conn, token = _get_owned_connection(c, user_id, connection_id)
    data = _call("/accounts/get", {"access_token": token})
    if not isinstance(data.get("accounts"), list):
        raise HTTPException(502, "Bank provider account response unavailable")
    accounts_data = data["accounts"]
    permitted = {a["account_id"] for a in accounts_data}
    original_cursor = conn["cursor"]
    for attempt in range(2):
        cursor = original_cursor
        added, modified, removed = [], [], []
        try:
            for _ in range(100):
                page = _call("/transactions/sync", {"access_token": token, "cursor": cursor, "count": 500})
                added.extend(page.get("added", []))
                modified.extend(page.get("modified", []))
                removed.extend(page.get("removed", []))
                cursor = page["next_cursor"]
                if not page.get("has_more"):
                    break
            else:
                raise HTTPException(502, "Bank provider sync exceeded page limit")
            break
        except HTTPException as e:
            if attempt == 0 and e.detail == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                continue
            raise
    now = int(time.time())
    with appdb.connect() as c:
        # Atomic compare-and-swap is essential here. A read followed by a later
        # write would allow overlapping workers (e.g. after lease expiry) to
        # both accept the same cursor and commit out-of-order transactions.
        update = appdb.execute(c, """UPDATE app_bank_connections
            SET cursor=?,last_synced_at=?,updated_at=?,status='connected'
            WHERE id=? AND user_id=? AND cursor IS NOT DISTINCT FROM ?""",
            (cursor, now, now, connection_id, user_id, original_cursor))
        if update.rowcount != 1:
            raise HTTPException(409, "Bank sync cursor changed; retry")
        _save_accounts(c, user_id, connection_id, accounts_data)
        # The /accounts/get list defines accounts with currently granted access.
        # Remove data for any account the user has deselected through Plaid.
        existing = appdb.execute(c, "SELECT id FROM app_bank_accounts WHERE connection_id=? AND user_id=?",
                                 (connection_id, user_id)).fetchall()
        for row in existing:
            if row["id"] not in permitted:
                appdb.execute(c, "DELETE FROM app_bank_transactions WHERE connection_id=? AND user_id=? AND account_id=?",
                              (connection_id, user_id, row["id"]))
                appdb.execute(c, "DELETE FROM app_bank_accounts WHERE connection_id=? AND user_id=? AND id=?",
                              (connection_id, user_id, row["id"]))
        # Deduplicate repeat transaction ids across pages. Later updates win.
        changes = {t["transaction_id"]: t for t in [*added, *modified]}
        for t in changes.values():
            if t["account_id"] not in permitted:
                continue
            category = (t.get("personal_finance_category") or {}).get("primary")
            account = next((a for a in accounts_data if a["account_id"] == t["account_id"]), {})
            balance = account.get("balances") or {}
            currency = t.get("iso_currency_code") or t.get("unofficial_currency_code") or balance.get("iso_currency_code") or balance.get("unofficial_currency_code")
            amount_text = _signed_decimal(t["amount"])
            appdb.execute(c, """INSERT INTO app_bank_transactions
                (user_id,id,connection_id,account_id,date,amount,name,merchant,category,pending,
                 pending_transaction_id,currency,amount_decimal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,id) DO UPDATE SET
                account_id=excluded.account_id,date=excluded.date,amount=excluded.amount,
                name=excluded.name,merchant=excluded.merchant,category=excluded.category,
                pending=excluded.pending,pending_transaction_id=excluded.pending_transaction_id,
                currency=excluded.currency,amount_decimal=excluded.amount_decimal""",
                (user_id,t["transaction_id"],connection_id,t["account_id"],t["date"],
                 float(Decimal(amount_text)),t.get("name"),t.get("merchant_name"),category,
                 int(bool(t.get("pending"))),t.get("pending_transaction_id"),currency,amount_text))
        for t in removed:
            appdb.execute(c, "DELETE FROM app_bank_transactions WHERE id=? AND user_id=? AND connection_id=?",
                          (t["transaction_id"], user_id, connection_id))
        # Even if the provider omits a removed pending transaction, posted linkage
        # tells us the authorization must not be counted twice.
        for t in changes.values():
            if t.get("pending_transaction_id") and not t.get("pending"):
                appdb.execute(c, "DELETE FROM app_bank_transactions WHERE id=? AND user_id=? AND connection_id=? AND pending=1",
                              (t["pending_transaction_id"], user_id, connection_id))
    return {"ok": True, "added": len(added), "modified": len(modified), "removed": len(removed)}


@router.post("/sync")
def sync(body: ConnectionId, who=Depends(require)):
    return sync_jobs.run_manual(who["id"], body.connection_id)


@router.post("/webhook/configure")
def configure_existing_webhook(body: ConnectionId, who=Depends(require)):
    """Attach the configured callback to an Item linked before it existed.

    The target is only read from server configuration and the Item must be
    owned by the logged-in user; never accept an arbitrary callback URL.
    """
    url = _webhook_target()
    if not url:
        raise HTTPException(503, "Set PLAID_WEBHOOK_URL before configuring an Item webhook")
    with appdb.connect() as c:
        conn, token = _get_owned_connection(c, who["id"], body.connection_id)
    result = _call("/item/webhook/update", {"access_token": token, "webhook": url})
    item = result.get("item") or {}
    if item.get("item_id") and item["item_id"] != conn["item_id"]:
        raise HTTPException(502, "Bank provider returned an unexpected Item")
    return {"configured": True}


@router.post("/sandbox-fire-webhook")
def fire_sandbox_webhook(body: ConnectionId, who=Depends(require)):
    """Ask Plaid Sandbox to deliver a signed SYNC_UPDATES_AVAILABLE webhook.

    Only requests an event; success does NOT prove the callback reached Ahead.
    Verify webhook arrival and worker processing separately.
    """
    if os.getenv("PLAID_ENV", "sandbox").lower() != "sandbox":
        raise HTTPException(403, "Test events are available only in Plaid Sandbox")
    if not _webhook_target():
        raise HTTPException(503, "Configure a public HTTPS PLAID_WEBHOOK_URL first")
    with appdb.connect() as c:
        _, token = _get_owned_connection(c, who["id"], body.connection_id)
    result = _call("/sandbox/item/fire_webhook", {
        "access_token": token,
        "webhook_type": "TRANSACTIONS",
        "webhook_code": "SYNC_UPDATES_AVAILABLE",
    })
    if result.get("webhook_fired") is not True:
        raise HTTPException(502, "Plaid Sandbox did not confirm test event request")
    return {"requested": True}


@router.post("/disconnect")
def disconnect(body: ConnectionId, who=Depends(require)):
    with appdb.connect() as c:
        conn, token = _get_owned_connection(c, who["id"], body.connection_id)
    _call("/item/remove", {"access_token": token})
    with appdb.connect() as c:
        appdb.execute(c, "DELETE FROM app_bank_transactions WHERE connection_id=? AND user_id=?",(body.connection_id,who["id"]))
        appdb.execute(c, "DELETE FROM app_bank_accounts WHERE connection_id=? AND user_id=?",(body.connection_id,who["id"]))
        appdb.execute(c, "DELETE FROM app_bank_connections WHERE id=? AND user_id=?",(body.connection_id,who["id"]))
        appdb.execute(c, "INSERT INTO app_audit VALUES (?,?,?,?,?)", (str(uuid.uuid4()),who["id"],"bank.disconnected",int(time.time()),body.connection_id))
    return {"ok": True}


def verify_webhook(signature, raw):
    """Verify Plaid's ES256 JWT and its SHA-256 binding to the *raw* body."""
    if not configured():
        raise HTTPException(503, "Bank provider is not configured")
    if not signature or len(signature) > 4096:
        raise HTTPException(401, "Missing or invalid bank webhook signature")
    try:
        header = jwt.get_unverified_header(signature)
        kid = header.get("kid")
        if header.get("alg") != "ES256" or not isinstance(kid, str) or not (1 <= len(kid) <= 160):
            raise ValueError("Unrecognized signature header")
        key = _call("/webhook_verification_key/get", {"key_id": kid})["key"]
        if key.get("kid") != kid or key.get("kty") != "EC" or key.get("crv") != "P-256":
            raise ValueError("Incorrect verification key")
        public_key = jwt.PyJWK.from_dict(key, algorithm="ES256").key
        claims = jwt.decode(signature, public_key, algorithms=["ES256"], options={"verify_aud": False})
        issued = claims.get("iat")
        now = int(time.time())
        if not isinstance(issued, int) or not (now - 300 <= issued <= now + 30):
            raise ValueError("Webhook signature expired")
        expected = claims.get("request_body_sha256", "")
        digest = hashlib.sha256(raw).hexdigest()
        if not isinstance(expected, str) or not hmac.compare_digest(expected, digest):
            raise ValueError("Webhook payload was changed")
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as e:
        raise HTTPException(401, "Invalid bank webhook signature") from e


@router.post("/webhook")
async def webhook(request: Request):
    """Public *only* to signed Plaid events; persist work and return promptly."""
    raw = await request.body()
    if len(raw) > 256_000:
        raise HTTPException(413, "Webhook payload too large")
    verify_webhook(request.headers.get("Plaid-Verification", ""), raw)
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("Expected object")
        item_id = event.get("item_id")
        event_type = event.get("webhook_type")
        code = event.get("webhook_code")
        if not all(isinstance(v, str) for v in (item_id, event_type, code)):
            raise ValueError("Incomplete event")
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(400, "Invalid webhook payload") from e
    with appdb.connect() as c:
        conn = appdb.execute(c, "SELECT id,user_id FROM app_bank_connections WHERE item_id=?", (item_id,)).fetchone()
        if not conn:
            return {"received": True}  # May be late delivery for a disconnected Item.
        if event_type == "TRANSACTIONS" and code == "SYNC_UPDATES_AVAILABLE":
            sync_jobs.enqueue(c, conn["user_id"], conn["id"])
        elif event_type == "ITEM" and code in ("ERROR", "PENDING_EXPIRATION", "USER_PERMISSION_REVOKED"):
            appdb.execute(c, "UPDATE app_bank_connections SET status='needs_attention',updated_at=? WHERE id=?",
                          (int(time.time()), conn["id"]))
    return {"received": True}
