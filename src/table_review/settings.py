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

    @property
    def lifecycle_journal_path(self):
        return self.journal_path or self.blob_root.parent / "lifecycle" / "journal.sqlite3"
