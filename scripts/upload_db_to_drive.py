"""Upload data/twstock.db to a Google Drive folder via rclone.

Uses SQLite's online-backup API to produce a consistent snapshot of the
live DB into a temp file, then rclone copies the snapshot to the target
Drive folder under a date-stamped name. The snapshot is self-contained
(no companion -wal/-shm needed) and safe under concurrent writers — the
backup API blocks until pages can be copied without holding the writer
lock.

Requires rclone installed and a remote named 'gdrive' configured.

Usage:
    uv run python scripts/upload_db_to_drive.py
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("upload_db")

DEFAULT_DB_PATH = Path("data/twstock.db")
DEFAULT_REMOTE = "gdrive"
DEFAULT_FOLDER_ID = "1bvaYhYT5qu5NncfDznsifaIxORdG7u5b"


def snapshot_db(src_path: Path, dest_path: Path) -> None:
    """Online backup of `src_path` into `dest_path` using sqlite3 backup API.

    Unlike `PRAGMA wal_checkpoint`, the backup API does not silently
    return on a busy checkpoint — it blocks (subject to timeout) until
    every page has been copied, so the resulting file is a complete
    standalone DB even under concurrent write traffic.
    """
    with closing(sqlite3.connect(str(src_path), timeout=30.0)) as src, \
            closing(sqlite3.connect(str(dest_path))) as dest:
        src.backup(dest)


def upload(snapshot_path: Path, remote: str, folder_id: str,
           dest_name: str) -> None:
    cmd = [
        "rclone", "copyto",
        str(snapshot_path),
        f"{remote}:{dest_name}",
        "--drive-root-folder-id", folder_id,
        "--stats-one-line",
        "--stats", "5s",
    ]
    logger.info("uploading %s → %s:[folder %s]/%s",
                snapshot_path, remote, folder_id, dest_name)
    subprocess.run(cmd, check=True)
    logger.info("upload complete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--folder-id", default=DEFAULT_FOLDER_ID)
    args = parser.parse_args()

    if not args.db.exists():
        logger.error("db not found: %s", args.db)
        return 2

    dest_name = f"twstock-{date.today().isoformat()}.db"

    with tempfile.NamedTemporaryFile(
        suffix=".db", prefix="twstock-snap-", delete=False
    ) as tmp:
        snapshot_path = Path(tmp.name)
    try:
        logger.info("snapshotting %s → %s", args.db, snapshot_path)
        snapshot_db(args.db, snapshot_path)
        upload(snapshot_path, args.remote, args.folder_id, dest_name)
    finally:
        snapshot_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
