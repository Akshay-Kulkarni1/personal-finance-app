"""Prove a local SQLite backup can be restored to an isolated test database.

Does NOT modify the live database. No rows/PII are printed or exported.
  cd backend && python restore_drill.py                 # backup + test restore
  cd backend && python restore_drill.py --backup backups/old.sqlite3
"""
import argparse
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

import appdb
from backup import backup


def restore_drill(source: Path | None = None):
    if appdb.is_pg():
        raise RuntimeError("For PostgreSQL, use a staging restore from managed PITR/pg_dump")
    with tempfile.TemporaryDirectory(prefix="ahead-restore-drill-") as scratch:
        root = Path(scratch)
        if source is None:
            source = backup(root / "fresh-backup.sqlite3")
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError("Backup does not exist")
        restored = root / "restored.sqlite3"
        # Opening a WAL-enabled live DB by copying the file directly is unsafe.
        # The input must be a snapshot made by backup.py (or equivalent).
        shutil.copyfile(source, restored)
        original_path = os.environ.get("APP_DB_PATH")
        try:
            os.environ["APP_DB_PATH"] = str(restored)
            with appdb.connect() as c:
                assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity check failed"
                assert c.execute("PRAGMA foreign_key_check").fetchall() == [], "Broken foreign keys"
                version = c.execute("SELECT MAX(version) FROM app_schema_migrations").fetchone()[0]
                assert version >= 5, "Missing Ahead schema migrations"
                c.execute("SELECT COUNT(*) FROM app_users").fetchone()
                c.execute("SELECT COUNT(*) FROM app_bank_transactions").fetchone()
        finally:
            if original_path is None:
                os.environ.pop("APP_DB_PATH", None)
            else:
                os.environ["APP_DB_PATH"] = original_path
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test-restore Ahead SQLite database into disposable storage")
    parser.add_argument("--backup", type=Path, help="Previously created consistent SQLite backup")
    args = parser.parse_args()
    restore_drill(args.backup)
    print("PASS: SQLite backup restored and validated in isolation; live data was not modified")
