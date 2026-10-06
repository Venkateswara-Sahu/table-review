"""Private operator commands. Key/password material is never an argument or output."""

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet

from table_review.lifecycle.backup import BackupError, create_backup, purge_backups, restore_backup
from table_review.lifecycle.journal import LifecycleJournal
from table_review.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("initialize-key", "create", "restore", "purge"))
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    settings = Settings(
        database_url=os.environ.get("TABLE_REVIEW_DATABASE_URL", ""),
        database_schema=os.environ.get("TABLE_REVIEW_DATABASE_SCHEMA"),
        blob_root=Path(os.environ.get("TABLE_REVIEW_BLOB_ROOT", "data/blobs")),
        journal_path=Path(os.environ["TABLE_REVIEW_JOURNAL_PATH"])
        if "TABLE_REVIEW_JOURNAL_PATH" in os.environ
        else None,
        backup_root=Path(os.environ.get("TABLE_REVIEW_BACKUP_ROOT", "data/backups")),
        backup_key_path=Path(os.environ["TABLE_REVIEW_BACKUP_KEY_PATH"])
        if "TABLE_REVIEW_BACKUP_KEY_PATH" in os.environ
        else None,
    )
    try:
        if args.action == "initialize-key":
            path = settings.backup_key_path
            if (
                path is None
                or path.resolve().is_relative_to(settings.blob_root.resolve())
                or path.resolve().is_relative_to(settings.backup_root.resolve())
            ):
                raise BackupError(
                    "Set TABLE_REVIEW_BACKUP_KEY_PATH outside private blobs and backups"
                )
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(Fernet.generate_key())
                output.flush()
                os.fsync(output.fileno())
            print("Backup key initialized; preserve it separately")
        elif args.action == "purge":
            print(f"Expired backups removed: {purge_backups(settings, datetime.now(UTC))}")
        else:
            if args.backup is None:
                parser.error("--backup is required for create/restore")
            if args.action == "create":
                result = create_backup(settings, args.backup, datetime.now(UTC))
                print(
                    f"Backup created; {result.blob_count} blobs; expiry {result.expires_at.isoformat()}"
                )
            else:
                result = restore_backup(
                    args.backup, LifecycleJournal(settings.lifecycle_journal_path), settings
                )
                print(result.model_dump_json())
    except (BackupError, OSError) as error:
        # No driver parameters, source paths, passwords or encryption keys.
        parser.exit(1, f"{type(error).__name__}: backup operation could not be completed\n")


if __name__ == "__main__":
    main()
