"""Upload data/twstock.db to a Google Drive folder via rclone.

Checkpoints the WAL into the main DB before upload so the snapshot is
self-contained. Names the uploaded file with today's date so each run keeps
a new dated copy.

Requires rclone installed and a remote named 'gdrive' configured (see README).

Usage:
    uv run python scripts/upload_db_to_drive.py
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("upload_db")

DEFAULT_DB_PATH = Path("data/twstock.db")
DEFAULT_REMOTE = "gdrive"
DEFAULT_FOLDER_ID = "1bvaYhYT5qu5NncfDznsifaIxORdG7u5b"


def checkpoint_wal(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def upload(db_path: Path, remote: str, folder_id: str) -> None:
    dest_name = f"twstock-{date.today().isoformat()}.db"
    cmd = [
        "rclone", "copyto",
        str(db_path),
        f"{remote}:{dest_name}",
        "--drive-root-folder-id", folder_id,
        "--stats-one-line",
        "--stats", "5s",
    ]
    logger.info("uploading %s → %s:[folder %s]/%s",
                db_path, remote, folder_id, dest_name)
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

    logger.info("checkpointing WAL into %s", args.db)
    checkpoint_wal(args.db)

    upload(args.db, args.remote, args.folder_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
