#!/usr/bin/env python3
"""Fail CI if sensitive local files are tracked in Git.

This is a filename/path policy, not a substitute for history scanning or
secret scanning. It deliberately never reads or prints secret contents.
"""
from pathlib import Path
import subprocess
import sys


def prohibited_paths(files):
    issues = []
    for raw in files:
        name = Path(raw).name.lower()
        parts = [segment.lower() for segment in Path(raw).parts]
        if (name in {'.env', '.env.local', '.env.production', '.env.development'}
            or (name.startswith('.env.') and not name.endswith('.example'))
            or name.endswith(('.sqlite', '.sqlite3', '.db', '.pem', '.p12', '.pfx', '.key'))
            or 'node_modules' in parts or '.next' in parts or '.venv' in parts
            or (len(parts) >= 2 and parts[0] == 'backend' and parts[1] == 'data')
        ):
            issues.append(raw)
    return issues


def main():
    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z'], capture_output=True)
    if result.returncode:
        print('BLOCKED: cannot inspect Git index; run inside a Git checkout', file=sys.stderr)
        return 2
    names = [n.decode('utf-8', errors='replace') for n in result.stdout.split(b'\0') if n]
    problems = prohibited_paths(names)
    if problems:
        print('BLOCKED: sensitive local paths are tracked by Git:', file=sys.stderr)
        for name in problems:
            print(' - ' + name, file=sys.stderr)
        return 1
    print(f'PASS: no prohibited local paths in {len(names)} tracked files (filename-only check)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
