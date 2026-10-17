import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator

from table_review.documents.types import ImmutableModel, SourceReference
from table_review.tables.context_types import ContextBinding, ContextSource
from table_review.tables.subtotal_types import SubtotalGroup

type CellRole = Literal[
    "data", "column_header", "row_header", "section", "value", "label", "header"
]
type ValueInterpretation = Literal["text", "en_us_number", "percent", "uncertain"]


class CellContent(ImmutableModel):
    id: str
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    raw_text: str
    text: str
    role: CellRole = "data"
    interpretation: ValueInterpretation = "en_us_number"
    reference: SourceReference
    source_references: tuple[SourceReference, ...] = ()

    @field_validator("interpretation", mode="before")
    @classmethod
    def legacy_interpretation(cls, value):
        return "en_us_number" if value in ("auto", "number") else value


class TableContent(ImmutableModel):
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    cells: tuple[CellContent, ...]
    header_rows: tuple[int, ...] = ()
    row_label_columns: tuple[int, ...] = ()
    unit_notes: tuple[str, ...] = ()
    source_reference: SourceReference | None = None
    context_sources: tuple[ContextSource, ...] = Field(default=(), max_length=32)
    context_bindings: tuple[ContextBinding, ...] = Field(default=(), max_length=40000)
    subtotal_groups: tuple[SubtotalGroup, ...] = Field(default=(), max_length=50)


def content_checksum(content: TableContent) -> str:
    canonical = content.model_dump(mode="json")
    # Empty extensions must not alter checksums of pre-context immutable revisions.
    for name in ("context_sources", "context_bindings", "subtotal_groups"):
        if not canonical[name]:
            canonical.pop(name)
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def infer_header_roles(cells: tuple[CellContent, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    rows = tuple(
        sorted(
            {
                r
                for c in cells
                if c.role == "column_header"
                for r in range(c.row, c.row + c.row_span)
            }
        )
    )
    columns = tuple(
        sorted(
            {
                col
                for c in cells
                if c.role == "row_header"
                for col in range(c.col, c.col + c.col_span)
            }
        )
    )
    return rows, columns
