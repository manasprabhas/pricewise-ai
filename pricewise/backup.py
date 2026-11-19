"""Backups and restores of the database file, safely.

Backup: SQLite's own online-backup API, so the copy is consistent even while the app is running.
Restore: the uploaded file is checked first (is it SQLite? does it have PriceWise's tables? does it pass
SQLite's integrity check?). The current database is kept as data/backups/before-restore-<time>.db, then the
restored file is upgraded to the current schema. Counts before and after are reported.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from . import db

REQUIRED = {"stores", "products", "receipts", "items"}


class RestoreError(ValueError):
    pass


def counts(path: Path | None = None) -> dict[str, int]:
    conn = sqlite3.connect(path or db.db_path())
    try:
        out = {}
        for t in ("receipts", "items", "products", "stores", "savings"):
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = 0
        return out
    finally:
        conn.close()


def make_backup() -> bytes:
    src = sqlite3.connect(db.db_path())
    with tempfile.TemporaryDirectory() as d:
        dst_path = Path(d) / "copy.db"
        dst = sqlite3.connect(dst_path)
        src.backup(dst)
        dst.close()
        src.close()
        return dst_path.read_bytes()


def check_file(data: bytes) -> dict[str, int]:
    if not data.startswith(b"SQLite format 3\x00"):
        raise RestoreError("That file isn't a PriceWise backup (not an SQLite database).")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "check.db"
        p.write_bytes(data)
        conn = sqlite3.connect(p)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = REQUIRED - tables
            if missing:
                raise RestoreError(f"That database is missing PriceWise tables: {', '.join(sorted(missing))}.")
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise RestoreError(f"The backup is damaged ({ok}).")
        finally:
            conn.close()
        return counts(p)


def restore(data: bytes) -> dict:
    expected = check_file(data)
    current = db.db_path()
    keep = None
    if current.exists():
        bdir = current.parent / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        keep = bdir / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
        shutil.copy2(current, keep)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(data)
    db.init_db()                     # upgrade an older backup to the current schema
    after = counts()
    if {k: after[k] for k in expected} != expected:
        raise RestoreError(f"Counts changed during restore: {expected} -> {after}. Your previous data is in {keep}.")
    return {"restored": after, "previous_copy": str(keep) if keep else None}
