#!/usr/bin/env python3
"""Ahead local diagnostics: no secrets printed, no external services contacted.

  python3 scripts/doctor-local.py --preflight  # before launching
  python3 scripts/doctor-local.py --wait-api   # launch readiness gate
  python3 scripts/doctor-local.py              # troubleshoot a running app
"""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent


def probe(url, timeout=2):
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.status, response.read(200).decode("utf-8", "replace")
    except HTTPError as e:
        return e.code, "HTTP error"
    except (URLError, TimeoutError, OSError):
        return None, "unreachable"


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def preflight():
    errors = []
    for command in ("node", "npm"):
        try:
            result = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=6)
            if result.returncode != 0:
                errors.append(f"{command} isn't running correctly")
            else:
                print(f"✓ {command}: {result.stdout.strip()}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            errors.append(f"{command} is missing (install Node.js LTS)")
    for port, service in ((8000, "FastAPI"), (3000, "Next.js")):
        if port_free(port):
            print(f"✓ Port {port} available for {service}")
        else:
            errors.append(f"Port {port} is occupied. Stop the old {service} process or change the port.")
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        import main  # noqa: F401
        print("✓ Backend modules import successfully")
    except Exception as e:
        errors.append(f"Backend cannot import: {type(e).__name__} (check backend/.venv dependencies)")
    env = ROOT / "backend" / ".env"
    if not env.exists():
        errors.append("backend/.env is missing (run scripts/configure-local.py)")
    else:
        print("✓ Local configuration exists")
    for error in errors:
        print(f"✗ {error}", file=sys.stderr)
    return 1 if errors else 0


def wait_api(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status, _ = probe("http://127.0.0.1:8000/ready", timeout=1)
        if status == 200:
            print("✓ Ahead backend is ready on http://127.0.0.1:8000")
            return 0
        time.sleep(.35)
    print("✗ Ahead backend did not become ready; check the backend process and its logs", file=sys.stderr)
    return 1


def running():
    urls = [
        ("Backend liveness", "http://127.0.0.1:8000/health"),
        ("Backend readiness", "http://127.0.0.1:8000/ready"),
        ("Frontend", "http://127.0.0.1:3000/login"),
        ("Frontend → API proxy", "http://127.0.0.1:3000/api/ahead/health"),
    ]
    good = True
    for label, url in urls:
        status, _ = probe(url, timeout=5)
        if status != 200:
            print(f"✗ {label}: {status or 'unreachable'}  {url}")
            good = False
        else:
            print(f"✓ {label}: HTTP {status}")
    if not good:
        print("Hint: run ./scripts/start-local.sh and inspect backend/data/logs/ for errors.")
    return 0 if good else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ahead local backend/frontend diagnostics")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--wait-api", action="store_true")
    parser.add_argument("--timeout", type=int, default=20)
    options = parser.parse_args()
    if options.preflight:
        sys.exit(preflight())
    if options.wait_api:
        sys.exit(wait_api(options.timeout))
    sys.exit(running())
