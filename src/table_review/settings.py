import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_pdf_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 500
    max_selected_pages: int = 5
    max_rows: int = 500
    max_columns: int = 60
    max_grid_slots: int = 10_000
    max_user_runs: int = 3
    max_attempts: int = 2
    task_timeout_seconds: int = 600
    lease_seconds: int = 60
    heartbeat_seconds: int = 15
    retention_days: int = 30
    worker_memory_bytes: int = 6 * 1024**3
    model_path: Path = Path("models")
    database_url: str = Field(default="", repr=False)
    database_schema: str | None = None
    blob_root: Path = Path("data/blobs")
    public_origin: str = "http://localhost:8000"
    secure_cookies: bool = True
    max_render_pixels: int = 16_000_000
    journal_path: Path | None = None
    initialize_journal: bool = False
    backup_key_path: Path | None = Field(default=None, repr=False)
    backup_root: Path = Path("data/backups")
    max_backup_bytes: int = 256 * 1024**2
    worker_pulse_path: Path | None = None

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ["TABLE_REVIEW_DATABASE_URL"],
            database_schema=os.environ.get("TABLE_REVIEW_DATABASE_SCHEMA"),
            blob_root=Path(os.environ.get("TABLE_REVIEW_BLOB_ROOT", "data/blobs")),
            model_path=Path(os.environ.get("TABLE_REVIEW_MODEL_PATH", "models")),
            journal_path=Path(os.environ["TABLE_REVIEW_JOURNAL_PATH"])
            if "TABLE_REVIEW_JOURNAL_PATH" in os.environ
            else None,
            backup_key_path=Path(os.environ["TABLE_REVIEW_BACKUP_KEY_PATH"])
            if "TABLE_REVIEW_BACKUP_KEY_PATH" in os.environ
            else None,
            backup_root=Path(os.environ.get("TABLE_REVIEW_BACKUP_ROOT", "data/backups")),
            worker_pulse_path=Path(os.environ["TABLE_REVIEW_WORKER_PULSE_PATH"])
            if "TABLE_REVIEW_WORKER_PULSE_PATH" in os.environ
            else None,
            public_origin=os.environ.get("TABLE_REVIEW_PUBLIC_ORIGIN", "http://localhost:8000"),
            secure_cookies=os.environ.get("TABLE_REVIEW_SECURE_COOKIES", "true").lower() != "false",
        )

    @property
    def pulse_path(self):
        return self.worker_pulse_path or self.blob_root.parent / "runtime" / "worker-pulse.json"

    @property
    def lifecycle_journal_path(self):
        return self.journal_path or self.blob_root.parent / "lifecycle" / "journal.sqlite3"

    @property
    def restore_pending_path(self):
        root = self.blob_root.absolute()
        return root.with_name(root.name + ".restore-pending")
