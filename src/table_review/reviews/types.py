from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from table_review.checks.types import Issue, IssueDecisionRecord
from table_review.documents.types import ImmutableModel


class ReviewSnapshot(ImmutableModel):
    id: UUID
    revision_id: UUID
    content_checksum: str
    label: Literal["reviewed", "reviewed_with_warnings"]
    checked_cell_ids: tuple[str, ...]
    issue_decisions: dict[str, IssueDecisionRecord]
    check_versions: dict[str, str]
    issues: tuple[Issue, ...] = ()
    checks_not_run: bool
    unresolved_count: int
    actor_id: UUID
    approved_at: datetime


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
