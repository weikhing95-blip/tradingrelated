"""Nightly SQLite backup (Phase 3A).

Creates a *consistent* snapshot of the live DB via SQLite's online backup API
(safe to run while the bot is writing), gzips it, and hands the file back for
off-volume storage or an owner DM. Railway's volume is durable but a single
volume is a single point of failure — an off-volume copy is the safety net.

All operations are best-effort at the call site: a backup failure must never
take the bot down (see ``scheduler.backup_job``).
"""

from __future__ import annotations

import gzip
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional


def create_backup(db_path: Path, dest_dir: Optional[Path] = None, *, now: Optional[float] = None) -> Path:
    """Snapshot ``db_path`` to a gzipped file and return its path.

    Uses ``sqlite3.Connection.backup`` (the online backup API) so the copy is
    transactionally consistent even while the polling jobs write to the DB. The
    ``seen`` table is included, so a restore does not cause a duplicate-flood on
    restart (the cold-start prime sees the items as already seen).
    """
    now = time.time() if now is None else now
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
    dest_dir = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    gz_path = dest_dir / f"{db_path.stem}-{stamp}.db.gz"

    tmp_fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=dest_dir)
    tmp_fd.close()
    tmp_db = Path(tmp_fd.name)
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(tmp_db))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        with open(tmp_db, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            f_out.writelines(f_in)
    finally:
        try:
            tmp_db.unlink()
        except OSError:
            pass
    return gz_path
