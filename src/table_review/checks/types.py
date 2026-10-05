from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from table_review.documents.types import ImmutableModel, PageBox, SourceReference
from table_review.extraction.types import SourceToken


class CheckContext(ImmutableModel):
    source_tokens: tuple[SourceToken, ...]
    source_regions: tuple[PageBox, ...]
    check_version: str = "1"
    source_available: bool = True


class Issue(ImmutableModel):
    id: UUID
    kind: Literal[
        "coverage_discrepancy",
        "duplicate_source_token",
        "placement_ambiguity",
        "structural_inconsistency",
        "limited_reference",
        "coverage_unavailable",
    ]
    affected_cell_ids: tuple[str, ...]
    evidence: dict[str, Any]
    fingerprint: str
    reason: str
    reference: SourceReference | None = None


class DecisionReason(ImmutableModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Record a reason after checking the source")
        return value.strip()


class Dismiss(DecisionReason):
    kind: Literal["dismiss"]


class Resolved(DecisionReason):
    kind: Literal["resolved"]


class Reopen(DecisionReason):
    kind: Literal["reopen"]


IssueDecisionInput = Annotated[Dismiss | Resolved | Reopen, Field(discriminator="kind")]


class IssueDecisionRecord(ImmutableModel):
    kind: Literal["dismiss", "resolved", "reopen"]
    reason: str
    actor_id: UUID
    decided_at: datetime
    issue_id: UUID
    fingerprint: str
    revision_id: UUID
