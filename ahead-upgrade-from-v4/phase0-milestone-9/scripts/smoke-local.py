#!/usr/bin/env python3
"""Non-financial end-to-end check through Ahead's actual Next.js API proxy.

Run when both servers are running: python3 scripts/smoke-local.py
Creates a random test user and deletes it. NEVER run against real deployment.
This does not use Plaid, authenticate to a bank, or move money.
"""
import argparse
from http.cookiejar import CookieJar
import json
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


def run(base="http://127.0.0.1:3000", wait=0, direct=False):
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1") or parsed.username or parsed.password:
        raise ValueError("This diagnostic is restricted to localhost/127.0.0.1, not deployed services")
    root = base.rstrip("/") + ("" if direct else "/api/ahead")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    token = ""
    password = secrets.token_urlsafe(27) + "_Strong"
    email = "ahead-m5-smoke-" + secrets.token_hex(10) + "@example.test"
    created = False

    def call(path, method="GET", payload=None, csrf=True):
        nonlocal token
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if csrf and token and method not in ("GET", "HEAD"):
            headers["X-CSRF-Token"] = token
        request = Request(root + path, data=json.dumps(payload).encode() if payload is not None else
                          (b"" if method == "POST" else None), headers=headers, method=method)
        try:
            with opener.open(request, timeout=10) as response:
                data = response.read(2_000_000)
                return response.status, json.loads(data) if data else None
        except HTTPError as exc:
            try:
                body = json.loads(exc.read(20_000))
            except (ValueError, UnicodeDecodeError):
                body = {}
            return exc.code, body

    if wait:
        until = time.monotonic() + wait
        while True:
            try:
                status, data = call("/health")
                if status == 200 and data == {"ok": True}:
                    break
            except (URLError, OSError, ValueError):
                pass
            if time.monotonic() >= until:
                raise RuntimeError("Frontend API proxy was not ready. Check the API and Next.js logs.")
            time.sleep(.5)
    status, data = call("/health")
    assert status == 200 and data == {"ok": True}, "Frontend-to-backend API proxy not working"
    print("PASS: " + ("direct backend" if direct else "browser-origin /api/ahead proxy") + " health")
    try:
        status, data = call("/auth/register", "POST", {"email": email, "password": password})
        assert status == 201 and "csrf_token" in data, "Registration did not create a session"
        token = data["csrf_token"]
        created = True
        print("PASS: registration sets cookie and CSRF token")
        status, _ = call("/auth/logout", "POST", csrf=False)
        assert status == 403, "Request without CSRF token unexpectedly succeeded"
        status, data = call("/auth/me")
        assert status == 200 and data["user"]["email"] == email
        print("PASS: cookie session survives HTTP routing and CSRF protection is active")
        status, data = call("/bank/accounts")
        assert status == 200 and data == {"accounts": []}, "Unexpected seeded bank data"
        print("PASS: new user sees no demo bank accounts")
        status, data = call("/auth/privacy/export", "POST", {"password": password})
        assert status == 200 and data["user"]["email"] == email and data["bank_transactions"] == []
        print("PASS: authenticated privacy export over HTTP")
        status, data = call("/auth/privacy/delete", "POST", {"password": password})
        assert status == 200 and data["deleted"] is True
        created = False
        status, _ = call("/auth/me")
        assert status == 401, "Deleted user retained a session"
        print("PASS: deletion revokes the session")
    finally:
        if created:
            # Best-effort cleanup if any assertion failed. Do not print secrets.
            try:
                call("/auth/privacy/delete", "POST", {"password": password})
            except Exception:
                print("NOTE: Diagnostic user cleanup may be needed in the local test database", file=sys.stderr)
    print("PASS: Milestone 5 local HTTP smoke test (no real bank connections)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exercise Ahead local registration, CSRF, privacy and session lifecycle")
    parser.add_argument("--base", default="http://127.0.0.1:3000")
    parser.add_argument("--wait", type=int, default=0, help="Wait up to N seconds for API readiness")
    parser.add_argument("--direct", action="store_true", help="Check FastAPI directly (does not test the Next.js proxy)")
    args = parser.parse_args()
    try:
        run(args.base, args.wait, args.direct)
    except Exception as error:
        print("FAIL: " + str(error), file=sys.stderr)
        print("Run ./scripts/doctor-local.sh and check backend/data/logs/api.log", file=sys.stderr)
        raise SystemExit(1)
