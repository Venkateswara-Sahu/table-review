import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _parse_retention_days(raw: str | None, deployment_mode: str) -> int | None:
    cleaned = (raw or "").strip().lower()
    if not cleaned:
        return None if deployment_mode == "local" else 30
    if cleaned in {"0", "none", "off", "false", "forever", "unlimited", "disabled"}:
        return None
    try:
        return int(cleaned)
    except ValueError as error:
        raise ValueError("TABLE_REVIEW_RETENTION_DAYS must be a positive integer or 'none'") from error


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_pdf_bytes: int = 50 * 1024 * 1024
    max_json_bytes: int = 2 * 1024 * 1024
    pdf_timeout_seconds: float = 30
    pdf_memory_bytes: int = 512 * 1024 * 1024
    pdf_wait_seconds: float = 2
    max_pdf_pages: int = 500
    max_selected_pages: int = 20
    max_rows: int = 500
    max_columns: int = 60
    max_grid_slots: int = 10_000
    max_user_runs: int = 3
    max_attempts: int = 2
    task_timeout_seconds: int = 600
    lease_seconds: int = 60
    heartbeat_seconds: int = 15
    retention_days: int | None = 30
    worker_memory_bytes: int = 6 * 1024**3
    model_path: Path = Path("models")
    database_url: str = Field(default="", repr=False)
    database_schema: str | None = None
    blob_root: Path = Path("data/blobs")
    public_origin: str = "http://localhost:8000"
    secure_cookies: bool = True
    deployment_mode: Literal["local", "hosted"] = "local"
    local_auto_login: bool = False
    max_render_pixels: int = 16_000_000
    journal_path: Path | None = None
    initialize_journal: bool = False
    backup_key_path: Path | None = Field(default=None, repr=False)
    backup_root: Path = Path("data/backups")
    max_backup_bytes: int = 256 * 1024**2
    worker_pulse_path: Path | None = None
    default_extraction_backend: Literal["auto", "docling", "native_fast", "vlm"] = "auto"
    extraction_device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    vlm_base_url: str | None = None
    vlm_api_key: str | None = Field(default=None, repr=False)
    vlm_model: str | None = None
    vlm_timeout_seconds: float = 120.0
    allow_vlm_in_hosted: bool = False

    @model_validator(mode="after")
    def hosted_boundary(self):
        if self.deployment_mode == "hosted":
            try:
                origin = urlsplit(self.public_origin)
                valid = (
                    origin.scheme == "https"
                    and bool(origin.hostname)
                    and origin.username is None
                    and origin.password is None
                    and not origin.path
                    and not origin.query
                    and not origin.fragment
                    and (origin.port is None or 1 <= origin.port <= 65535)
                    and not any(c.isspace() for c in self.public_origin)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Hosted mode requires an exact HTTPS origin without a path")
            if not self.secure_cookies:
                raise ValueError("Hosted mode requires secure cookies")
            if self.local_auto_login:
                raise ValueError("Hosted mode cannot enable local auto-login")
        if self.retention_days is not None and self.retention_days < 1:
            raise ValueError("Retention days must be at least 1 or disabled (None)")
        if self.vlm_base_url is not None:
            try:
                vlm_url = urlsplit(self.vlm_base_url)
                valid_vlm = (
                    vlm_url.scheme in ("http", "https")
                    and bool(vlm_url.hostname)
                    and not any(c.isspace() for c in self.vlm_base_url)
                )
            except ValueError:
                valid_vlm = False
            if not valid_vlm:
                raise ValueError("VLM base URL must use http or https with a valid hostname")
        return self

    @classmethod
    def from_env(cls):
        deployment_mode = os.environ.get("TABLE_REVIEW_DEPLOYMENT_MODE", "local")
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
            deployment_mode=deployment_mode,
            local_auto_login=(
                os.environ.get("TABLE_REVIEW_LOCAL_AUTO_LOGIN", "false").lower() == "true"
            ),
            retention_days=_parse_retention_days(
                os.environ.get("TABLE_REVIEW_RETENTION_DAYS"), deployment_mode
            ),
            default_extraction_backend=os.environ.get("TABLE_REVIEW_EXTRACTION_BACKEND", "auto"),
            extraction_device=os.environ.get("TABLE_REVIEW_EXTRACTION_DEVICE", "auto"),
            vlm_base_url=os.environ.get("TABLE_REVIEW_VLM_BASE_URL") or None,
            vlm_api_key=os.environ.get("TABLE_REVIEW_VLM_API_KEY") or None,
            vlm_model=os.environ.get("TABLE_REVIEW_VLM_MODEL") or None,
            vlm_timeout_seconds=float(os.environ.get("TABLE_REVIEW_VLM_TIMEOUT_SECONDS", "120.0")),
            allow_vlm_in_hosted=(
                os.environ.get("TABLE_REVIEW_ALLOW_VLM_IN_HOSTED", "false").lower() == "true"
            ),
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
