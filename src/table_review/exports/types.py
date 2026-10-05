from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from table_review.documents.types import ImmutableModel
from table_review.reviews.types import ReviewSnapshot, ReviewState
from table_review.tables.types import TableContent, content_checksum

type ExportFormat = Literal["workbook", "csv_package"]


class ExportInput(ImmutableModel):
    content: TableContent
    revision_id: UUID
    review_snapshot: ReviewSnapshot | None = None
    captured_review: ReviewState | None = None
    source_manifest: dict[str, Any] = Field(default_factory=dict)
    generation_manifest: dict[str, Any] = Field(default_factory=dict)
    edit_events: tuple[dict[str, Any], ...] = ()
    issues: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def matching_snapshot(self):
        if self.review_snapshot and (
            self.review_snapshot.revision_id != self.revision_id
            or self.review_snapshot.content_checksum != content_checksum(self.content)
        ):
            raise ValueError("Approval does not match the requested revision content")
        if self.captured_review and self.captured_review.revision_id != self.revision_id:
            raise ValueError("Review state does not match the requested revision")
        return self


class ExportArtifact(ImmutableModel):
    format: ExportFormat
    path: Path
    sha256: str
    manifest: dict[str, Any]


class ExportRecord(ImmutableModel):
    id: UUID
    table_id: UUID
    revision_id: UUID
    format: ExportFormat
    state: str
    failure_code: str | None = None
    sha256: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)


def export_manifest(data: ExportInput, format: ExportFormat):
    snapshot, review = data.review_snapshot, data.captured_review
    return {
        "export_version": "1",
        "format": format,
        "revision_id": str(data.revision_id),
        "content_checksum": content_checksum(data.content),
        "approval_id": str(snapshot.id) if snapshot else None,
        "review_status": snapshot.label if snapshot else "draft",
        "review": (snapshot or review).model_dump(mode="json") if snapshot or review else None,
        "check_versions": (snapshot or review).check_versions
        if snapshot or review
        else {"checks": "not_run"},
        "unresolved_count": (snapshot or review).unresolved_count if snapshot or review else 1,
        "source": data.source_manifest,
        "generator": data.generation_manifest,
        "header_rows": list(data.content.header_rows),
        "row_label_columns": list(data.content.row_label_columns),
        "unit_notes": list(data.content.unit_notes),
        "rows": data.content.rows,
        "columns": data.content.columns,
        "source_reference": data.content.source_reference.model_dump(mode="json")
        if data.content.source_reference
        else None,
        "issues": list(data.issues),
        "csv_text_escape": "Dangerous literal text is prefixed with [text] . cells.json preserves exact text and per-cell escape flags.",
        "numeric_policy": "Explicit English/US interpretation, at most 15 authored significant digits, Decimal round trip; no scale conversion; signed negative zero remains text.",
    }
