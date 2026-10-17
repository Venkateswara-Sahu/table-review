from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from table_review.documents.types import ImmutableModel


class SubtotalTerm(ImmutableModel):
    cell_id: str = Field(min_length=1, max_length=200)
    sign: Literal[-1, 1]


class SubtotalGroup(ImmutableModel):
    id: UUID
    name: str = Field(min_length=1, max_length=80)
    total_cell_id: str = Field(min_length=1, max_length=200)
    terms: tuple[SubtotalTerm, ...] = Field(min_length=1, max_length=100)
    tolerance: str = Field(default="0", pattern=r"^(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,6})?$")
    evidence_fingerprint: str = ""
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def distinct(self):
        ids = [term.cell_id for term in self.terms]
        if not self.name.strip() or len(set(ids)) != len(ids) or self.total_cell_id in ids:
            raise ValueError("Name the subtotal and select unique inputs separate from the total")
        return self


class SubtotalResult(ImmutableModel):
    policy: str = "declared_sum_v1"
    group: SubtotalGroup
    state: Literal["match", "difference", "needs_confirmation", "unavailable"]
    reason: str
    scope: dict[str, str] = Field(default_factory=dict)
    inputs: tuple[dict[str, Any], ...] = ()
    total: dict[str, Any] = Field(default_factory=dict)
    input_sum: str | None = None
    reported_total: str | None = None
    difference: str | None = None


class SubtotalResults(ImmutableModel):
    items: tuple[SubtotalResult, ...]
