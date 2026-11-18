"""User identity, short-lived authentication challenges, TOTP MFA and cookie sessions.

All secrets and reset tokens stay on the server. Mail delivery is required in
production; local development can inspect the restricted outbox directory.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

import appdb
import mailer

router = APIRouter(prefix="/auth", tags=["authentication"])
COOKIE = "ahead_session"
SESSION_SECONDS = 60 * 60 * 24 * 7
MAX_SESSIONS_PER_USER = 10
HASHER = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)
DUMMY_HASH = HASHER.hash("not-a-real-user-password")
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


class EmailOnly(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TokenOnly(BaseModel):
    token: str = Field(min_length=20, max_length=300)


class ResetPassword(BaseModel):
    token: str = Field(min_length=20, max_length=300)
    password: str = Field(min_length=12, max_length=256)


class MfaComplete(BaseModel):
    challenge: str = Field(min_length=20, max_length=300)
    code: str = Field(min_length=6, max_length=32)


class MfaCode(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaPassword(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class MfaDisable(MfaPassword):
    code: str = Field(min_length=6, max_length=32)


class RevokeSession(BaseModel):
    session_id: str = Field(min_length=10, max_length=60)


def _normalize(email):
    email = email.strip().lower()
    if not EMAIL.fullmatch(email):
        raise HTTPException(422, "Enter a valid email address")
    return email


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _cookie(response, token):
    response.set_cookie(COOKIE, token, httponly=True, secure=os.getenv("AHEAD_ENV") == "production",
                        samesite="lax", max_age=SESSION_SECONDS, path="/")


def _make_session(c, user_id, response):
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    now = int(time.time())
    session_id = str(uuid.uuid4())
    # Cap browser sessions per account. Revoke the oldest first, with a stable
    # tie-breaker; a stolen forgotten session should not live forever.
    appdb.execute(c, "DELETE FROM app_sessions WHERE user_id=? AND expires_at<=?", (user_id, now))
    appdb.execute(c, """INSERT INTO app_sessions
        (token_hash,user_id,csrf,expires_at,created_at,id,created_at_ns) VALUES (?,?,?,?,?,?,?)""",
                  (_digest(token), user_id, csrf, now + SESSION_SECONDS, now, session_id, time.time_ns()))
    sessions = appdb.execute(c, """SELECT token_hash FROM app_sessions WHERE user_id=? AND id<>?
        ORDER BY created_at_ns DESC,created_at DESC,id DESC""", (user_id, session_id)).fetchall()
    for expired in sessions[MAX_SESSIONS_PER_USER - 1:]:
        appdb.execute(c, "DELETE FROM app_sessions WHERE token_hash=?", (expired["token_hash"],))
    _cookie(response, token)
    return csrf


def session(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with appdb.connect() as c:
        row = appdb.execute(c, """SELECT u.id, u.email, u.email_verified_at,
               CASE WHEN u.mfa_secret IS NULL THEN 0 ELSE 1 END AS mfa_enabled,
               s.id AS session_id, s.csrf, s.expires_at
             FROM app_sessions s JOIN app_users u ON u.id=s.user_id
             WHERE s.token_hash=?""", (_digest(token),)).fetchone()
        if row is None or row["expires_at"] <= int(time.time()):
            return None
        return dict(row)


def require(request: Request):
    who = session(request)
    if who is None:
        raise HTTPException(401, "Sign in to continue")
    return who


def _account_info(c, user_id, csrf):
    u = appdb.execute(c, """SELECT id,email,email_verified_at,
          CASE WHEN mfa_secret IS NULL THEN 0 ELSE 1 END AS mfa_enabled
          FROM app_users WHERE id=?""", (user_id,)).fetchone()
    return {"user": {"id": u["id"], "email": u["email"],
                     "email_verified": bool(u["email_verified_at"]), "mfa_enabled": bool(u["mfa_enabled"])},
            "csrf_token": csrf}


def _fingerprint(request, email):
    ip = request.client.host if request.client else "unknown"
    return _digest(ip + ":" + email)


def _too_many(c, key):
    row = appdb.execute(c, "SELECT count,start_at FROM app_login_attempts WHERE key=?", (key,)).fetchone()
    return bool(row and int(time.time()) - row["start_at"] < 900 and row["count"] >= 8)


def _failed(c, key):
    now = int(time.time())
    # Atomic upsert: concurrent failures cannot overwrite each other's counts
    # in PostgreSQL, unlike a SELECT followed by UPDATE.
    appdb.execute(c, """INSERT INTO app_login_attempts (key,count,start_at) VALUES (?,1,?)
        ON CONFLICT(key) DO UPDATE SET
          count=CASE WHEN app_login_attempts.start_at>? THEN app_login_attempts.count+1 ELSE 1 END,
          start_at=CASE WHEN app_login_attempts.start_at>? THEN app_login_attempts.start_at ELSE excluded.start_at END""",
                  (key, now, now - 900, now - 900))


def _verify_password(hash_value, password):
    try:
        return HASHER.verify(hash_value or DUMMY_HASH, password)
    except VerificationError:
        return False


def _issue(c, uid, purpose, ttl):
    token = secrets.token_urlsafe(40)
    now = int(time.time())
    appdb.execute(c, "DELETE FROM app_auth_tokens WHERE user_id=? AND purpose=?", (uid, purpose))
    appdb.execute(c, "INSERT INTO app_auth_tokens VALUES (?,?,?,?,?)",
                  (_digest(token), uid, purpose, now + ttl, now))
    return token


def _redeem(c, token, purpose):
    row = appdb.execute(c, "SELECT user_id,expires_at FROM app_auth_tokens WHERE token_hash=? AND purpose=?",
                        (_digest(token), purpose)).fetchone()
    if not row or row["expires_at"] <= int(time.time()):
        raise HTTPException(400, "This link is invalid or has expired")
    appdb.execute(c, "DELETE FROM app_auth_tokens WHERE token_hash=?", (_digest(token),))
    return row["user_id"]


def _encryption():
    key = os.getenv("AHEAD_ENCRYPTION_KEY", "")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise HTTPException(503, "MFA encryption is not configured") from e


def _totp(secret, when=None):
    """RFC 6238 SHA-1 6-digit OTP, 30-second step; compatible with authenticator apps."""
    now = int(time.time()) if when is None else int(when)
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, (now // 30).to_bytes(8, "big"), hashlib.sha1).digest()
    off = digest[-1] & 15
    value = int.from_bytes(digest[off:off+4], "big") & 0x7fffffff
    return f"{value % 1000000:06d}"


def _valid_totp(secret, code):
    return len(code) == 6 and code.isascii() and code.isdigit() and any(
        hmac.compare_digest(_totp(secret, int(time.time()) + delta), code)
        for delta in (-30, 0, 30))


def _mfa_check(c, uid, encrypted, code, allow_recovery=True):
    try:
        secret = _encryption().decrypt(encrypted.encode()).decode()
    except InvalidToken as e:
        raise HTTPException(503, "MFA configuration unavailable") from e
    if _valid_totp(secret, code):
        return True
    if allow_recovery and len(code) >= 12:
        digest = _digest(code.replace("-", "").upper())
        row = appdb.execute(c, "SELECT code_hash FROM app_mfa_recovery_codes WHERE user_id=? AND code_hash=?", (uid, digest)).fetchone()
        if row:
            appdb.execute(c, "DELETE FROM app_mfa_recovery_codes WHERE user_id=? AND code_hash=?", (uid, digest))
            return True
    return False


@router.post("/register", status_code=201)
def register(body: Credentials, response: Response, request: Request):
    email = _normalize(body.email)
    uid = str(uuid.uuid4())
    with appdb.connect() as c:
        key = "register:" + _digest(request.client.host if request.client else "unknown")
        if _too_many(c, key):
            raise HTTPException(429, "Too many registrations. Try again later")
        if appdb.execute(c, "SELECT id FROM app_users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "An account with that email already exists")
        try:
            appdb.execute(c, "INSERT INTO app_users (id,email,password_hash,created_at) VALUES (?,?,?,?)",
                          (uid, email, HASHER.hash(body.password), int(time.time())))
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, "An account with that email already exists") from e
            raise
        _failed(c, key)  # Coarse registration abuse control (8 per 15 minutes/IP).
        token = _issue(c, uid, "verify", 24 * 3600)
        # Enforce configured mail delivery for deployment rather than producing
        # users who cannot verify or recover access.
        mailer.send_link(email, "verify", token)
        csrf = _make_session(c, uid, response)
        return _account_info(c, uid, csrf)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    email = _normalize(body.email)
    key = _fingerprint(request, email)
    # Persistent account-wide throttle blocks attempts spread over multiple IPs.
    # Use the same generic response regardless of account existence.
    account_key = "login-account:" + _digest(email)
    with appdb.connect() as c:
        if _too_many(c, key) or _too_many(c, account_key):
            raise HTTPException(429, "Too many attempts. Try again in 15 minutes")
        row = appdb.execute(c, "SELECT id,password_hash,mfa_secret FROM app_users WHERE email=?", (email,)).fetchone()
        if not _verify_password(row["password_hash"] if row else None, body.password) or row is None:
            _failed(c, key)
            _failed(c, account_key)
            c.commit()  # Store failed attempts even when HTTPException rolls back context.
            raise HTTPException(401, "Invalid email or password")
        appdb.execute(c, "DELETE FROM app_login_attempts WHERE key IN (?,?)", (key, account_key))
        if row["mfa_secret"]:
            challenge = secrets.token_urlsafe(40)
            appdb.execute(c, "INSERT INTO app_mfa_challenges VALUES (?,?,?,?)",
                          (_digest(challenge), row["id"], 0, int(time.time()) + 300))
            return {"mfa_required": True, "challenge": challenge}
        csrf = _make_session(c, row["id"], response)
        return _account_info(c, row["id"], csrf)


@router.post("/mfa/complete")
def mfa_complete(body: MfaComplete, response: Response):
    with appdb.connect() as c:
        row = appdb.execute(c, """SELECT m.user_id,m.attempts,m.expires_at,u.mfa_secret
             FROM app_mfa_challenges m JOIN app_users u ON u.id=m.user_id
             WHERE m.token_hash=?""", (_digest(body.challenge),)).fetchone()
        if not row or row["expires_at"] <= int(time.time()) or row["attempts"] >= 5:
            raise HTTPException(401, "MFA challenge expired or invalid")
        key = "mfa:" + row["user_id"]
        if _too_many(c, key):
            raise HTTPException(429, "Too many authentication attempts")
        if not _mfa_check(c, row["user_id"], row["mfa_secret"], body.code):
            _failed(c, key)
            appdb.execute(c, "UPDATE app_mfa_challenges SET attempts=attempts+1 WHERE token_hash=?", (_digest(body.challenge),))
            c.commit()
            raise HTTPException(401, "Incorrect authentication code")
        appdb.execute(c, "DELETE FROM app_login_attempts WHERE key=?", (key,))
        appdb.execute(c, "DELETE FROM app_mfa_challenges WHERE token_hash=?", (_digest(body.challenge),))
        csrf = _make_session(c, row["user_id"], response)
        return _account_info(c, row["user_id"], csrf)


@router.get("/me")
def me(request: Request):
    who = require(request)
    return {"user": {"id": who["id"], "email": who["email"],
                     "email_verified": bool(who["email_verified_at"]),
                     "mfa_enabled": bool(who["mfa_enabled"])}, "csrf_token": who["csrf"]}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with appdb.connect() as c:
            appdb.execute(c, "DELETE FROM app_sessions WHERE token_hash=?", (_digest(token),))
    response.delete_cookie(COOKIE, httponly=True, secure=os.getenv("AHEAD_ENV") == "production",
                           samesite="lax", path="/")
    return {"ok": True}


@router.post("/verify-email")
def verify_email(body: TokenOnly):
    with appdb.connect() as c:
        uid = _redeem(c, body.token, "verify")
        appdb.execute(c, "UPDATE app_users SET email_verified_at=? WHERE id=?", (int(time.time()), uid))
    return {"ok": True}


@router.post("/resend-verification")
def resend_verification(request: Request):
    who = require(request)
    with appdb.connect() as c:
        row = appdb.execute(c, "SELECT email_verified_at FROM app_users WHERE id=?", (who["id"],)).fetchone()
        if row and row["email_verified_at"]:
            return {"ok": True}
        # Limit re-issuance to one per minute.
        recent = appdb.execute(c, "SELECT created_at FROM app_auth_tokens WHERE user_id=? AND purpose='verify'", (who["id"],)).fetchone()
        if recent and time.time() - recent["created_at"] < 60:
            return {"ok": True}
        token = _issue(c, who["id"], "verify", 24 * 3600)
        mailer.send_link(who["email"], "verify", token)
    return {"ok": True}


@router.post("/forgot-password")
def forgot_password(body: EmailOnly, request: Request):
    email = _normalize(body.email)
    with appdb.connect() as c:
        key = "reset:" + _digest(request.client.host if request.client else "unknown")
        if _too_many(c, key):
            return {"ok": True, "message": "If an account exists, we'll send an email shortly."}
        _failed(c, key)
        row = appdb.execute(c, "SELECT id FROM app_users WHERE email=?", (email,)).fetchone()
        if row:
            recent = appdb.execute(c, "SELECT created_at FROM app_auth_tokens WHERE user_id=? AND purpose='reset'", (row["id"],)).fetchone()
            if not recent or time.time() - recent["created_at"] >= 60:
                token = _issue(c, row["id"], "reset", 1800)
                mailer.send_link(email, "reset", token)
    return {"ok": True, "message": "If an account exists, we'll send an email shortly."}


@router.post("/reset-password")
def reset_password(body: ResetPassword):
    with appdb.connect() as c:
        uid = _redeem(c, body.token, "reset")
        appdb.execute(c, "UPDATE app_users SET password_hash=? WHERE id=?", (HASHER.hash(body.password), uid))
        appdb.execute(c, "DELETE FROM app_sessions WHERE user_id=?", (uid,))
        appdb.execute(c, "DELETE FROM app_mfa_challenges WHERE user_id=?", (uid,))
    return {"ok": True, "message": "Password updated. Sign in again."}


@router.post("/mfa/setup")
def mfa_setup(body: MfaPassword, request: Request):
    who = require(request)
    with appdb.connect() as c:
        row = appdb.execute(c, "SELECT password_hash,mfa_secret FROM app_users WHERE id=?", (who["id"],)).fetchone()
        if not _verify_password(row["password_hash"], body.password):
            raise HTTPException(401, "Incorrect password")
        if row["mfa_secret"]:
            raise HTTPException(409, "MFA already enabled")
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        appdb.execute(c, "UPDATE app_users SET mfa_pending=?,mfa_pending_expires=? WHERE id=?",
                      (_encryption().encrypt(secret.encode()).decode(), int(time.time()) + 600, who["id"]))
    return {"secret": secret, "otpauth_uri": f"otpauth://totp/Ahead:{quote(who['email'], safe='')}?secret={secret}&issuer=Ahead&digits=6&period=30"}


@router.post("/mfa/enable")
def mfa_enable(body: MfaCode, request: Request):
    who = require(request)
    with appdb.connect() as c:
        row = appdb.execute(c, "SELECT mfa_pending,mfa_pending_expires,mfa_secret FROM app_users WHERE id=?", (who["id"],)).fetchone()
        if row["mfa_secret"] or not row["mfa_pending"] or (row["mfa_pending_expires"] or 0) <= int(time.time()):
            raise HTTPException(400, "MFA setup expired; start again")
        try:
            secret = _encryption().decrypt(row["mfa_pending"].encode()).decode()
        except InvalidToken as e:
            raise HTTPException(503, "MFA setup unavailable") from e
        key = "mfa-enable:" + who["id"]
        if _too_many(c, key):
            raise HTTPException(429, "Too many authentication attempts")
        if not _valid_totp(secret, body.code):
            _failed(c, key)
            c.commit()
            raise HTTPException(400, "Incorrect authenticator code")
        appdb.execute(c, "DELETE FROM app_login_attempts WHERE key=?", (key,))
        codes = [secrets.token_hex(8).upper() for _ in range(10)]
        appdb.execute(c, "UPDATE app_users SET mfa_secret=mfa_pending,mfa_pending=NULL,mfa_pending_expires=NULL WHERE id=?", (who["id"],))
        appdb.execute(c, "DELETE FROM app_mfa_recovery_codes WHERE user_id=?", (who["id"],))
        for code in codes:
            appdb.execute(c, "INSERT INTO app_mfa_recovery_codes VALUES (?,?)", (who["id"], _digest(code)))
        # Current session stays active. Other sessions end when MFA is enabled.
        appdb.execute(c, "DELETE FROM app_sessions WHERE user_id=? AND id<>?", (who["id"], who["session_id"]))
    return {"ok": True, "recovery_codes": codes}


@router.post("/mfa/disable")
def mfa_disable(body: MfaDisable, request: Request):
    who = require(request)
    with appdb.connect() as c:
        row = appdb.execute(c, "SELECT password_hash,mfa_secret FROM app_users WHERE id=?", (who["id"],)).fetchone()
        if not row["mfa_secret"] or not _verify_password(row["password_hash"], body.password):
            raise HTTPException(401, "Incorrect credentials")
        key = "mfa-disable:" + who["id"]
        if _too_many(c, key):
            raise HTTPException(429, "Too many authentication attempts")
        if not _mfa_check(c, who["id"], row["mfa_secret"], body.code):
            _failed(c, key)
            c.commit()
            raise HTTPException(401, "Incorrect authentication code")
        appdb.execute(c, "DELETE FROM app_login_attempts WHERE key=?", (key,))
        appdb.execute(c, "UPDATE app_users SET mfa_secret=NULL,mfa_pending=NULL,mfa_pending_expires=NULL WHERE id=?", (who["id"],))
        appdb.execute(c, "DELETE FROM app_mfa_recovery_codes WHERE user_id=?", (who["id"],))
        appdb.execute(c, "DELETE FROM app_sessions WHERE user_id=? AND id<>?", (who["id"], who["session_id"]))
    return {"ok": True}


@router.get("/sessions")
def sessions(request: Request):
    who = require(request)
    with appdb.connect() as c:
        rows = appdb.execute(c, "SELECT id,created_at,expires_at FROM app_sessions WHERE user_id=? AND expires_at>? ORDER BY created_at DESC", (who["id"], int(time.time()))).fetchall()
    return {"sessions": [{"id": r["id"], "created_at": r["created_at"], "expires_at": r["expires_at"],
                           "current": r["id"] == who["session_id"]} for r in rows]}


@router.post("/sessions/revoke")
def revoke_session(body: RevokeSession, request: Request, response: Response):
    who = require(request)
    with appdb.connect() as c:
        appdb.execute(c, "DELETE FROM app_sessions WHERE user_id=? AND id=?", (who["id"], body.session_id))
    if body.session_id == who["session_id"]:
        response.delete_cookie(COOKIE, httponly=True, secure=os.getenv("AHEAD_ENV") == "production", samesite="lax", path="/")
    return {"ok": True}


def csrf_valid(request, who):
    return hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), who["csrf"])
