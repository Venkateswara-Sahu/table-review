"""Literal context evidence, separate from numeric interpretation or accounting rules."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from table_review.documents.types import ImmutableModel, SourceReference

type ContextKind = Literal["period", "currency", "scale", "measure"]


class ContextSource(ImmutableModel):
    id: str = Field(max_length=200)
    text: str = Field(max_length=800)
    raw_text: str = Field(max_length=800)
    references: tuple[SourceReference, ...] = Field(min_length=1, max_length=32)
    origin: Literal["extracted", "current_draft"]
    truncated: bool = False


class ContextProposal(ImmutableModel):
    id: str
    kind: ContextKind
    value: str = Field(max_length=800)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=4)


class ContextBinding(ContextProposal):
    cell_id: str
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None


class CellContext(ImmutableModel):
    cell_id: str
    column_headers: tuple[ContextSource, ...]
    row_headers: tuple[ContextSource, ...]
    sources: tuple[ContextSource, ...]
    proposals: tuple[ContextProposal, ...]
    confirmed: tuple[ContextBinding, ...]
    warnings: tuple[str, ...] = ()
