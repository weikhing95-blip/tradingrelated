#!/usr/bin/env python3
"""Restore the Mag 7 News Bot SQLite DB from a backup (Phase 3A).

Usage:
    python scripts/restore_db.py BACKUP[.db.gz|.db] --out /data/mag7bot.db

Takes a backup produced by the nightly job (gzipped) or a plain .db, writes it
to the target path, and verifies the `seen` table is intact — because `seen` is
what prevents a duplicate-flood on the next start (the cold-start prime treats
already-seen items as seen and does not re-fire them).

Refuses to overwrite an existing target unless --force is given.
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def _decompress_if_needed(src: Path) -> Path:
    """Return a path to a plain .db file (decompressing a .gz into a temp file)."""
    with open(src, "rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":  # gzip magic
        tmp = Path(tempfile.mkstemp(suffix=".db")[1])
        with gzip.open(src, "rb") as f_in, open(tmp, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        return tmp
    return src


def _verify(db_path: Path) -> int:
    """Confirm the restored DB opens and the `seen` table exists; return its row count."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = {"seen", "events", "feed"} - tables
        if missing:
            raise SystemExit(f"❌ Restore invalid — missing tables: {sorted(missing)}")
        return conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", type=Path, help="backup file (.db.gz or .db)")
    ap.add_argument("--out", type=Path, required=True, help="target DB path (e.g. /data/mag7bot.db)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing target DB")
    args = ap.parse_args()

    if not args.backup.exists():
        raise SystemExit(f"❌ Backup not found: {args.backup}")
    if args.out.exists() and not args.force:
        raise SystemExit(f"❌ Target {args.out} exists — pass --force to overwrite.")

    plain = _decompress_if_needed(args.backup)
    seen_count = _verify(plain)  # verify BEFORE clobbering the target

    args.out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(plain, args.out)
    if plain != args.backup:
        plain.unlink(missing_ok=True)

    print(f"✅ Restored {args.out} from {args.backup}")
    print(f"   `seen` table intact: {seen_count} row(s) — cold-start prime will NOT re-flood.")
    print("   Restart the bot; it will resume without duplicate alerts.")


if __name__ == "__main__":
    sys.exit(main())
