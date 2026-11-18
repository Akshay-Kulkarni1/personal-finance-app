"""Ahead production configuration gate. Safe to run in CI/deploy start scripts.

Checks configuration only: it intentionally does not dial banks, SMTP or the
public frontend. It never prints credentials or URLs containing secrets.
"""
import os
from urllib.parse import urlsplit, parse_qs
import ipaddress

from cryptography.fernet import Fernet


def _https_origin(value):
    if not value:
        return None
    try:
        p = urlsplit(value)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment:
            return None
        if p.path not in ("", "/"):
            return None
        if p.port and p.port not in (443,):
            return None
        return f"https://{p.hostname.lower()}"
    except ValueError:
        return None


def _postgres_production_url(value):
    """Reject obviously unsafe DB destinations and non-TLS connections.

    This only checks a declared URL, not the actual PostgreSQL TLS certificate.
    Database reachability and certificate validation require a staging test.
    """
    try:
        p = urlsplit(value)
        if p.scheme not in ("postgresql", "postgres") or not p.hostname or p.path in ("", "/"):
            return False
        hostname = p.hostname.lower().rstrip(".")
        if hostname in ("localhost", "0.0.0.0", "::1") or hostname.endswith(".localhost"):
            return False
        try:
            if ipaddress.ip_address(hostname).is_loopback or ipaddress.ip_address(hostname).is_unspecified:
                return False
        except ValueError:
            pass  # Managed private-hostname databases are allowed.
        options = parse_qs(p.query, keep_blank_values=True)
        modes = options.get("sslmode", [])
        mode = modes[0].lower() if len(modes) == 1 else ""
        # An explicitly declared TLS requirement is needed, even if a PG
        # driver's current default happens to negotiate encryption.
        return mode in ("require", "verify-ca", "verify-full")
    except ValueError:
        return False


def configuration_issues(env=None):
    e = env if env is not None else os.environ
    errors = []
    if e.get("AHEAD_ENV") != "production":
        errors.append("AHEAD_ENV must be production")
    if e.get("AHEAD_DEMO_MODE", "false").lower() != "false":
        errors.append("AHEAD_DEMO_MODE must be false")
    if not _postgres_production_url(e.get("APP_DATABASE_URL", "")):
        errors.append("APP_DATABASE_URL must point to a non-loopback PostgreSQL database with explicit sslmode=require or stronger")
    try:
        Fernet(e.get("AHEAD_ENCRYPTION_KEY", "").encode())
    except (ValueError, TypeError):
        errors.append("AHEAD_ENCRYPTION_KEY must be a valid persistent Fernet key")
    app_url = _https_origin(e.get("PUBLIC_APP_URL", ""))
    if not app_url:
        errors.append("PUBLIC_APP_URL must be a canonical HTTPS origin")
    raw_origins = e.get("FRONTEND_ORIGINS", "")
    origins = [x.strip() for x in raw_origins.split(",") if x.strip()]
    if not origins or any(_https_origin(x) != x.rstrip("/") for x in origins):
        errors.append("FRONTEND_ORIGINS must list only explicit HTTPS origins")
    if app_url and app_url not in origins:
        errors.append("FRONTEND_ORIGINS must include PUBLIC_APP_URL")
    if not e.get("SMTP_HOST") or e.get("SMTP_HOST", "").strip() in ("localhost", "127.0.0.1"):
        errors.append("SMTP_HOST must be configured for production email delivery")
    if bool(e.get("SMTP_USERNAME")) != bool(e.get("SMTP_PASSWORD")):
        errors.append("SMTP_USERNAME and SMTP_PASSWORD must be configured together")
    mail_from = e.get("MAIL_FROM", "")
    if "@" not in mail_from or "example.com" in mail_from or "localhost" in mail_from:
        errors.append("MAIL_FROM must use a real sender domain")
    enabled = e.get("AHEAD_BANK_CONNECTIONS_ENABLED", "true").lower()
    if enabled not in ("true", "false"):
        errors.append("AHEAD_BANK_CONNECTIONS_ENABLED must be true or false")
    if enabled != "false" and (not e.get("PLAID_CLIENT_ID") or not e.get("PLAID_SECRET")):
        errors.append("PLAID_CLIENT_ID and PLAID_SECRET are needed for enabled bank connections")
    if e.get("PLAID_ENV") != "production":
        errors.append("PLAID_ENV must be production (never Sandbox with live accounts)")
    webhook = e.get("PLAID_WEBHOOK_URL", "").strip()
    if webhook:
        p = urlsplit(webhook)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment:
            errors.append("PLAID_WEBHOOK_URL must be a valid HTTPS endpoint")
    if len(e.get("AHEAD_OPS_TOKEN", "")) < 32:
        errors.append("AHEAD_OPS_TOKEN must be 32+ characters")
    return errors


def main():
    issues = configuration_issues()
    if issues:
        print("Ahead production configuration: BLOCKED")
        for issue in issues:
            print(" - " + issue)
        return 1
    print("Ahead production configuration: PASS (configuration only; external services not tested)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
