from typing import Literal

from pydantic import Field

from table_review.documents.types import ImmutableModel, SourceReference


class CellContent(ImmutableModel):
    id: str
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    raw_text: str
    text: str
    role: Literal["data", "column_header", "row_header", "section"] = "data"
    interpretation: Literal["auto", "text", "number", "uncertain"] = "auto"
    reference: SourceReference


class TableContent(ImmutableModel):
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    cells: tuple[CellContent, ...]
    header_rows: tuple[int, ...] = ()
    row_label_columns: tuple[int, ...] = ()
    unit_notes: tuple[str, ...] = ()
