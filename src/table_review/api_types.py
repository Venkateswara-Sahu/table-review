"""Public response schemas: storage locations are not browser contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from table_review.accounts.types import Actor
from table_review.storage.models import TableRecord
from table_review.tables.types import TableContent


class APIModel(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


def public_manifest(value):
    if isinstance(value, dict):
        return {
            key: public_manifest(item)
            for key, item in value.items()
            if key not in {"source_path", "artifacts_path", "blob_key", "image_path", "scratch"}
        }
    if isinstance(value, (list, tuple)):
        return [public_manifest(item) for item in value]
    return value


class ErrorEnvelope(APIModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ReadinessComponents(APIModel):
    database: bool
    journal: bool
    worker: bool
    models: bool


class ReadinessView(APIModel):
    ready: bool
    components: ReadinessComponents


class SessionView(APIModel):
    actor: Actor
    csrf_token: str


class SessionCreated(SessionView):
    expires_at: datetime


class DocumentView(APIModel):
    id: UUID
    filename: str
    source_sha256: str
    byte_count: int
    page_count: int
    preparation_state: str
    created_at: datetime
    last_foreground_at: datetime


class DocumentList(APIModel):
    items: tuple[DocumentView, ...]


class PageView(APIModel):
    page_no: int
    width: float
    height: float
    digital_text_present: bool
    requires_ocr: bool = False
    extraction_supported: bool
    unsupported_reason: str | None


class RevisionView(APIModel):
    id: UUID
    table_id: UUID
    parent_revision_id: UUID | None
    content: TableContent
    checksum: str
    extraction_manifest: dict[str, Any]
    created_at: datetime

    @field_serializer("extraction_manifest")
    def manifest(self, value):
        return public_manifest(value)


class TableView(APIModel):
    table: TableRecord
    revision: RevisionView


class TableList(APIModel):
    items: tuple[TableRecord, ...]


class TaskView(APIModel):
    id: UUID
    document_id: UUID
    kind: str
    input: dict[str, Any]
    state: str
    attempt_count: int
    cancellation_requested: bool
    failure_code: str | None
    result: dict[str, Any]
    created_at: datetime

    @field_serializer("input", "result")
    def safe_details(self, value):
        return public_manifest(value)


class TaskList(APIModel):
    items: tuple[TaskView, ...]


class RunView(APIModel):
    id: UUID
    task_id: UUID
    manifest: dict[str, Any]
    table_ids: tuple[UUID, ...]
    warnings: tuple[str, ...]

    @field_serializer("manifest")
    def safe_manifest(self, value):
        return public_manifest(value)


class RunList(APIModel):
    items: tuple[RunView, ...]


class EditEventView(APIModel):
    id: UUID
    base_revision_id: UUID
    result_revision_id: UUID
    operation: dict[str, Any]
    affected_cell_ids: tuple[str, ...]
    structural_change: bool
    created_at: datetime


class EditHistory(APIModel):
    items: tuple[EditEventView, ...]
