from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from table_review.documents.types import ImmutableModel


class ProcessingLimits(ImmutableModel):
    timeout_seconds: float = Field(default=600, gt=0)
    concurrency: int = Field(default=1, ge=1, le=1)
    memory_bytes: int = Field(default=6 * 1024**3, gt=0)
    heartbeat_seconds: float = Field(default=15, gt=0, le=15)
    poll_seconds: float = Field(default=0.1, gt=0, le=1)


class TaskRecord(ImmutableModel):
    id: UUID
    owner_id: UUID
    document_id: UUID
    kind: str
    input: dict[str, Any]
    state: str
    attempt_count: int
    cancellation_requested: bool
    failure_code: str | None
    result: dict[str, Any]
    created_at: datetime


class TaskLease(ImmutableModel):
    task_id: UUID
    token: UUID
    expires_at: datetime
    attempt_no: int


class TaskOutcome(ImmutableModel):
    status: Literal["succeeded", "failed", "cancelled"]
    result: dict[str, Any] = Field(default_factory=dict)
    failure_code: str | None = None
    # Internal typed extraction payload is consumed only by fenced publication.
    extraction: dict[str, Any] | None = None
