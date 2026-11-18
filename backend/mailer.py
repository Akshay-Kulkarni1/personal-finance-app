"""Minimal email delivery adapter: SMTP in deployment; private file outbox locally.

Never returns verification/reset tokens through HTTP or application logs.
"""
from email.message import EmailMessage
import os
from pathlib import Path
import smtplib
import time
import uuid

from config import ROOT


def send_link(recipient: str, purpose: str, token: str):
    base = os.getenv("PUBLIC_APP_URL", "http://localhost:3000").rstrip("/")
    page = "verify-email" if purpose == "verify" else "reset-password"
    url = f"{base}/{page}#token={token}"
    subject = "Verify your Ahead email" if purpose == "verify" else "Reset your Ahead password"
    msg = EmailMessage()
    msg["From"] = os.getenv("MAIL_FROM", "Ahead <noreply@localhost>")
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(f"Open this one-time link to {purpose} your Ahead account:\n{url}\n\nIf you didn't request it, ignore this email.\n")
    host = os.getenv("SMTP_HOST")
    if host:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as smtp:
            smtp.starttls()
            if os.getenv("SMTP_USERNAME"):
                smtp.login(os.environ["SMTP_USERNAME"], os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    elif os.getenv("AHEAD_ENV") == "production":
        raise RuntimeError("SMTP_HOST is required for email verification and password recovery in production")
    else:
        # Explicit local-only outbox. Never enable this in production.
        outbox = Path(os.getenv("DEV_MAIL_OUTBOX") or str(ROOT / "data" / "mail-outbox"))
        outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
        filepath = outbox / f"{int(time.time())}-{uuid.uuid4().hex}.eml"
        filepath.write_bytes(msg.as_bytes())
        filepath.chmod(0o600)
