"""Consistent, permission-restricted SQLite backups for local deployments.

PostgreSQL installations should use pg_dump/PITR per operations documentation.
Does not copy live SQLite WAL files or dump secret values to stdout.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

import appdb


def backup(destination: Path):
    if appdb.is_pg():
        raise RuntimeError("Use PostgreSQL pg_dump / managed backups for APP_DATABASE_URL deployments")
    appdb.initialize()
    _, source = appdb._settings()
    destination = destination.resolve()
    if destination == source.resolve():
        raise ValueError("Destination cannot be the live database")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise FileExistsError("Backup target already exists: refusing to overwrite")
    # Restrict permissions before writing any sensitive account data.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError("Backup failed integrity check")
        os.chmod(destination, 0o600)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create verified consistent Ahead SQLite backup")
    parser.add_argument("--output", type=Path, help="Destination filename; default backups/ahead-<UTC>.sqlite3")
    args = parser.parse_args()
    output = args.output or Path("backups") / ("ahead-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sqlite3")
    print(f"Verified backup written to {backup(output)}")
