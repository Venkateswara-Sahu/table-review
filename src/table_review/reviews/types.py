from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from table_review.checks.types import Issue, IssueDecisionRecord
from table_review.documents.types import ImmutableModel, SourceReference

type CellVerdict = Literal["confirmed_value", "confirmed_blank", "unreadable", "unreviewed"]
type ApprovalMode = Literal["standard", "strict_audit"]


class BulkCellReviewInput(ImmutableModel):
    scope: Literal["clean_all", "row", "column", "cells"] = "clean_all"
    row: int | None = Field(default=None, ge=0)
    col: int | None = Field(default=None, ge=0)
    cell_ids: tuple[str, ...] = Field(default=(), max_length=10_000)
    skip_flagged: bool = True
    include_blanks: bool = False

    @model_validator(mode="after")
    def valid_scope(self):
        if self.scope == "row" and self.row is None:
            raise ValueError("Specify a row index for row bulk review")
        if self.scope == "column" and self.col is None:
            raise ValueError("Specify a column index for column bulk review")
        if self.scope == "cells" and not self.cell_ids:
            raise ValueError("Select at least one cell for bulk review")
        return self


class CellReviewInput(ImmutableModel):
    cell_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    verdict: CellVerdict
    reason: str = Field(default="", max_length=1000)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def unreadable_reason(self):
        if self.verdict == "unreadable" and not self.reason:
            raise ValueError("Explain why the source value is unreadable")
        return self


class CellReviewRecord(ImmutableModel):
    verdict: CellVerdict
    reason: str = ""
    revision_id: UUID
    actor_id: UUID | None = None
    decided_at: datetime | None = None
    source_references: tuple[SourceReference, ...]
    origin: Literal["explicit", "bulk", "legacy"] = "explicit"


class ReviewSnapshot(ImmutableModel):
    id: UUID
    revision_id: UUID
    content_checksum: str
    label: Literal["reviewed", "reviewed_with_warnings"]
    approval_mode: ApprovalMode = "strict_audit"
    checked_cell_ids: tuple[str, ...]
    issue_decisions: dict[str, IssueDecisionRecord]
    check_versions: dict[str, str]
    issues: tuple[Issue, ...] = ()
    checks_not_run: bool
    unresolved_count: int
    actor_id: UUID
    approved_at: datetime
    cell_reviews: dict[str, CellReviewRecord] = Field(default_factory=dict)
    unreadable_count: int = 0


class ReviewState(ImmutableModel):
    revision_id: UUID
    checked_cell_ids: tuple[str, ...] = ()
    issue_decisions: dict[str, IssueDecisionRecord] = Field(default_factory=dict)
    check_versions: dict[str, str] = Field(default_factory=lambda: {"checks": "not_run"})
    issues: tuple[Issue, ...] = ()
    checks_not_run: bool = True
    unresolved_count: int = 1
    generation: int = 0
    current_approval: ReviewSnapshot | None = None
    cell_reviews: dict[str, CellReviewRecord] = Field(default_factory=dict)
    unreadable_count: int = 0


class CellReviewEvent(ImmutableModel):
    id: UUID
    revision_id: UUID
    actor_id: UUID
    cell_ids: tuple[str, ...]
    checked: bool
    created_at: datetime
    cell_reviews: dict[str, CellReviewRecord] = Field(default_factory=dict)


class CellReviewHistory(ImmutableModel):
    items: tuple[CellReviewEvent, ...]
    offset: int
    has_more: bool


class TimelineEvent(ImmutableModel):
    id: UUID
    table_id: UUID
    revision_id: UUID
    sequence: int
    kind: Literal[
        "coverage_start",
        "checks_changed",
        "cell_review",
        "issue_decision",
        "approval",
        "edit",
        "table_visibility",
    ]
    actor_id: UUID | None
    recorded_at: datetime
    cell_ids: tuple[str, ...]
    details: dict[str, Any]


class ReviewTimeline(ImmutableModel):
    items: tuple[TimelineEvent, ...]
    through_sequence: int
    next_after_sequence: int
    has_more: bool
    coverage: Literal["recorded_since_boundary", "legacy_only"]
