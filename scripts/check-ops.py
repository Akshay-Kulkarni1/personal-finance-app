#!/usr/bin/env python3
"""Non-destructive Ahead operator probe. Requires a private operator token.

Exit 0 when DB, worker heartbeat, queue are healthy. Exit 2 if an operator
needs to intervene. Only supports HTTPS or loopback HTTP, never sends tokens to
arbitrary plaintext HTTP services. Suitable for authenticated scheduled checks.
"""
import argparse
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def safe_url(value):
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != '/ops/status':
        raise ValueError('Operational URL must end in /ops/status without credentials or query')
    if not parsed.hostname or not parsed.port and parsed.netloc.endswith(':'):
        raise ValueError('Missing operator host')
    if parsed.scheme == 'https':
        return value
    if parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1'):
        return value
    raise ValueError('Operator endpoint requires HTTPS, except on loopback')


def assess(status):
    issues = []
    if status.get('database') != 'ok':
        issues.append('Database status is not healthy')
    if not status.get('worker_observed_recently'):
        issues.append('Bank synchronization worker heartbeat is stale or missing')
    if int(status.get('overdue_jobs') or 0) > 0:
        issues.append('Bank synchronization queue has overdue jobs')
    if int(status.get('dead_letter_jobs') or 0) > 0:
        issues.append('Bank synchronization jobs need manual retry')
    if int((status.get('sync_jobs') or {}).get('needs_attention') or 0) > 0:
        issues.append('Some connections require user attention')
    return issues


def main():
    p = argparse.ArgumentParser(description='Read-only authenticated Ahead operations check')
    p.add_argument('--url', default=os.getenv('AHEAD_OPS_URL', 'http://127.0.0.1:8000/ops/status'))
    args = p.parse_args()
    token = os.environ.get('AHEAD_OPS_TOKEN', '')
    if len(token) < 32:
        print('BLOCKED: configure AHEAD_OPS_TOKEN (not printed)')
        return 2
    try:
        safe_url(args.url)
        req = Request(args.url, headers={'X-Ahead-Ops-Token': token, 'Accept': 'application/json'})
        with urlopen(req, timeout=8) as response:
            data = json.load(response)
        issues = assess(data)
    except (ValueError, OSError, json.JSONDecodeError, TypeError) as e:
        # Do not print raw URL/token or remote response: it could contain secrets.
        print('FAIL: operator endpoint unavailable or returned invalid data (' + type(e).__name__ + ')')
        return 2
    if issues:
        for issue in issues:
            print('ALERT: ' + issue)
        return 2
    print('PASS: database, bank worker and sync queue checks healthy')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
