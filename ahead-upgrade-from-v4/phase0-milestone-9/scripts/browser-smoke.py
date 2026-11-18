#!/usr/bin/env python3
"""Milestone 6: real Chromium end-to-end registration + privacy flow.

Requires a running Next.js frontend with a live FastAPI rewrite and the
Playwright Python client + Chromium installed. Test accounts are removed at the
end; no Plaid credentials or bank connection is required.

Local optional setup (in a disposable development environment):
    python -m pip install playwright
    python -m playwright install chromium
    python scripts/browser-smoke.py --base http://127.0.0.1:3000

CI runs this test after its Next.js/FastAPI HTTP smoke test.
"""
import argparse
import sys
import uuid
from urllib.parse import urlsplit


def run(base):
    parsed = urlsplit(base)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.scheme != "http":
        raise ValueError("Browser smoke test is restricted to the local development server")
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError as exc:
        raise RuntimeError("Install Playwright: python -m pip install playwright; python -m playwright install chromium") from exc
    email = f"ahead-m6-browser-{uuid.uuid4().hex[:12]}@example.test"
    password = "Ahead-Local-Browser-Test-Password-1234"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(base_url=base, accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(20000)
        try:
            page.goto("/login", wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Welcome to Ahead")).to_be_visible()
            page.get_by_role("button", name="New to Ahead? Create an account").click()
            page.get_by_label("Email", exact=True).fill(email)
            page.get_by_label("Password (12+ characters)").fill(password)
            page.get_by_role("button", name="Create account").click()
            expect(page.get_by_role("heading", name="Your financial home")).to_be_visible()
            expect(page.get_by_text(f"Signed in as {email}")).to_be_visible()
            expect(page.get_by_role("heading", name="Connect your first bank account")).to_be_visible()
            print("PASS: registration from real Chromium, authenticated personal home, no seeded bank data")

            page.goto("/accounts", wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Connected banks")).to_be_visible()
            expect(page.get_by_text("No banks connected yet.")).to_be_visible()
            print("PASS: bank connection screen renders without Plaid credentials")

            page.goto("/settings/security", wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Security settings")).to_be_visible()
            expect(page.get_by_role("heading", name="Active sessions")).to_be_visible()
            print("PASS: authenticated security settings")

            page.goto("/settings/privacy", wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Your data & privacy")).to_be_visible()
            page.get_by_label("Current password").fill(password)
            with page.expect_download() as info:
                page.get_by_role("button", name="Export my data").click()
            download = info.value
            assert download.suggested_filename.endswith(".json"), "Expected JSON personal export"
            expect(page.get_by_text("Your data export has been prepared.", exact=False)).to_be_visible()
            print("PASS: privacy export produces browser download")

            page.get_by_label("Type DELETE to confirm").fill("DELETE")
            page.get_by_role("button", name="Permanently delete active account data").click()
            expect(page.get_by_role("heading", name="Welcome to Ahead")).to_be_visible()
            page.get_by_label("Email", exact=True).fill(email)
            page.get_by_label("Password (12+ characters)").fill(password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("Invalid email or password")
            print("PASS: deletion redirects to login and prevents signing in again")
        except Exception:
            # Don't emit private test account details or browser screenshots.
            raise
        finally:
            # Best-effort cleanup if a UI assertion fails after registration.
            # This only touches the browser-test user's own account.
            try:
                session = context.request.get(base + "/api/ahead/auth/me", timeout=4000)
                if session.ok:
                    csrf = session.json().get("csrf_token", "")
                    context.request.post(
                        base + "/api/ahead/auth/privacy/delete",
                        headers={"X-CSRF-Token": csrf},
                        data={"password": password}, timeout=4000,
                    )
            except Exception:
                print("NOTE: Browser test account cleanup was inconclusive; check local test accounts", file=sys.stderr)
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="Test Ahead registration and deletion in Chromium")
    parser.add_argument("--base", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    try:
        run(args.base.rstrip("/"))
    except Exception as exc:
        print(f"FAIL: browser smoke test ({type(exc).__name__}): {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
