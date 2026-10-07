from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5

from pydantic import Field, TypeAdapter

from table_review.documents.types import ImmutableModel, PageBox, SourceReference
from table_review.settings import Settings
from table_review.tables.types import CellContent, TableContent, ValueInterpretation


class SetText(ImmutableModel):
    kind: Literal["set_text"]
    cell_id: str
    text: str


class AxisEdit(ImmutableModel):
    kind: Literal["insert_row", "delete_row", "insert_column", "delete_column"]
    index: int = Field(ge=0)


class Merge(ImmutableModel):
    kind: Literal["merge"]
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)


class Split(ImmutableModel):
    kind: Literal["split"]
    cell_id: str


class SetHeaders(ImmutableModel):
    kind: Literal["set_headers"]
    header_rows: tuple[int, ...]
    row_label_columns: tuple[int, ...]


class SetNotes(ImmutableModel):
    kind: Literal["set_notes"]
    unit_notes: tuple[str, ...]


class SetInterpretation(ImmutableModel):
    kind: Literal["set_interpretation"]
    cell_id: str
    interpretation: ValueInterpretation


EditOperation = Annotated[
    SetText | AxisEdit | Merge | Split | SetHeaders | SetNotes | SetInterpretation,
    Field(discriminator="kind"),
]
_EDIT = TypeAdapter(EditOperation)


def parse_edit(payload: dict) -> EditOperation:
    return _EDIT.validate_python(payload)


class EditResult(ImmutableModel):
    content: TableContent
    affected_cell_ids: tuple[str, ...]
    structural_change: bool


def validate_grid(content: TableContent) -> None:
    limits = Settings()
    errors = []
    if content.rows < 1 or content.columns < 1:
        errors.append("Table dimensions must be positive")
    if content.rows > limits.max_rows:
        errors.append("Table exceeds maximum rows")
    if content.columns > limits.max_columns:
        errors.append("Table exceeds maximum columns")
    if content.rows * content.columns > limits.max_grid_slots:
        errors.append("Table exceeds maximum grid slots")
    if errors:
        raise ValueError("; ".join(errors))
    occupied, ids = set(), set()
    for cell in content.cells:
        if cell.row < 0 or cell.col < 0 or cell.row_span < 1 or cell.col_span < 1:
            raise ValueError("Cell coordinates and spans must be positive/in bounds")
        if cell.id in ids:
            raise ValueError("Duplicate cell ID")
        ids.add(cell.id)
        if cell.row + cell.row_span > content.rows or cell.col + cell.col_span > content.columns:
            raise ValueError("Cell span is outside the table")
        for row in range(cell.row, cell.row + cell.row_span):
            for col in range(cell.col, cell.col + cell.col_span):
                if (row, col) in occupied:
                    raise ValueError("Cell spans overlap")
                occupied.add((row, col))
    if (
        len(set(content.header_rows)) != len(content.header_rows)
        or any(not 0 <= r < content.rows for r in content.header_rows)
        or len(set(content.row_label_columns)) != len(content.row_label_columns)
        or any(not 0 <= c < content.columns for c in content.row_label_columns)
    ):
        raise ValueError("Invalid header or row-label indices")


def expand_grid(content: TableContent):
    """Slots point to physical cells; covered slots never duplicate cell values."""
    validate_grid(content)
    slots = [[None] * content.columns for _ in range(content.rows)]
    for cell in content.cells:
        for r in range(cell.row, cell.row + cell.row_span):
            for c in range(cell.col, cell.col + cell.col_span):
                slots[r][c] = cell
    return tuple(tuple(row) for row in slots)


def _blank(row, col, reference, namespace=None):
    box = reference.box if reference.precision == "table" else None
    coarse = SourceReference(
        page=reference.page, precision="table" if box else "page", origin="user", box=box
    )
    identity = uuid5(namespace, f"{row}:{col}") if namespace else uuid4()
    return CellContent(id=str(identity), row=row, col=col, raw_text="", text="", reference=coarse)


def _merged_reference(cells):
    first = cells[0].reference
    if all(c.reference.box and c.reference.page == first.page for c in cells):
        boxes = [c.reference.box for c in cells]
        box = PageBox(
            page=first.page,
            left=min(b.left for b in boxes),
            top=min(b.top for b in boxes),
            right=max(b.right for b in boxes),
            bottom=max(b.bottom for b in boxes),
            page_width=boxes[0].page_width,
            page_height=boxes[0].page_height,
        )
        return SourceReference(page=first.page, precision="table", origin="user", box=box)
    return SourceReference(page=first.page, precision="page", origin="user")


def apply_edit(
    content: TableContent, operation: EditOperation, *, cell_namespace: UUID | None = None
) -> EditResult:
    validate_grid(content)
    cells = list(content.cells)
    structural = False
    updates = {}
    kind = operation.kind
    if isinstance(operation, (SetText, SetInterpretation, Split)):
        matches = [c for c in cells if c.id == operation.cell_id]
        if not matches:
            raise ValueError("Unknown cell ID")
        target = matches[0]
        if isinstance(operation, SetText):
            cells = [
                c.model_copy(update={"text": operation.text}) if c.id == target.id else c
                for c in cells
            ]
        elif isinstance(operation, SetInterpretation):
            cells = [
                c.model_copy(update={"interpretation": operation.interpretation})
                if c.id == target.id
                else c
                for c in cells
            ]
        else:
            if target.row_span * target.col_span == 1:
                raise ValueError("Cell is not a merged span")
            structural = True
            cells = [c for c in cells if c.id != target.id]
            cells.append(target.model_copy(update={"row_span": 1, "col_span": 1}))
            for r in range(target.row, target.row + target.row_span):
                for c in range(target.col, target.col + target.col_span):
                    if (r, c) != (target.row, target.col):
                        cells.append(_blank(r, c, target.reference, cell_namespace))
    elif isinstance(operation, AxisEdit):
        structural = True
        axis, dimension, span = (
            ("row", "rows", "row_span") if kind.endswith("row") else ("col", "columns", "col_span")
        )
        count = getattr(content, dimension)
        insert = kind.startswith("insert")
        if operation.index > count - (not insert) or (not insert and count == 1):
            raise ValueError("Invalid insertion/deletion index")
        updates[dimension] = count + (1 if insert else -1)
        changed = []
        for cell in cells:
            start, length = getattr(cell, axis), getattr(cell, span)
            if insert:
                values = (
                    {axis: start + 1}
                    if start >= operation.index
                    else {span: length + 1}
                    if start < operation.index < start + length
                    else {}
                )
            else:
                if start <= operation.index < start + length:
                    if length == 1:
                        continue
                    values = {span: length - 1}
                else:
                    values = {axis: start - 1} if start > operation.index else {}
            changed.append(cell.model_copy(update=values))
        cells = changed
        header_key = "header_rows" if axis == "row" else "row_label_columns"
        headers = getattr(content, header_key)
        updates[header_key] = tuple(
            h + (1 if insert else -1) if h >= operation.index + (not insert) else h
            for h in headers
            if insert or h != operation.index
        )
        if insert:
            temporary = content.model_copy(update={**updates, "cells": tuple(cells)})
            grid = expand_grid(temporary)
            reference = content.source_reference or (
                content.cells[0].reference if content.cells else None
            )
            if reference is None:
                raise ValueError("An empty manual table must record its source page")
            for i in range(content.columns if axis == "row" else content.rows):
                r, c = (operation.index, i) if axis == "row" else (i, operation.index)
                if grid[r][c] is None:
                    cells.append(_blank(r, c, reference, cell_namespace))
    elif isinstance(operation, Merge):
        structural = True
        endr, endc = operation.row + operation.rows, operation.col + operation.columns
        if endr > content.rows or endc > content.columns or operation.rows * operation.columns == 1:
            raise ValueError("Invalid merge rectangle")
        selected = []
        for cell in cells:
            if (
                cell.row < endr
                and cell.row + cell.row_span > operation.row
                and cell.col < endc
                and cell.col + cell.col_span > operation.col
            ):
                if (
                    cell.row < operation.row
                    or cell.col < operation.col
                    or cell.row + cell.row_span > endr
                    or cell.col + cell.col_span > endc
                ):
                    raise ValueError("Merge intersects an existing span")
                selected.append(cell)
        if not selected:
            raise ValueError("Merge needs at least one physical cell")
        selected.sort(key=lambda c: (c.row, c.col, c.id))
        anchor = selected[0]
        references = tuple(
            ref for cell in selected for ref in (cell.source_references or (cell.reference,))
        )
        merged = anchor.model_copy(
            update={
                "row": operation.row,
                "col": operation.col,
                "row_span": operation.rows,
                "col_span": operation.columns,
                "text": "\n".join(c.text for c in selected if c.text),
                "raw_text": "\n".join(c.raw_text for c in selected if c.raw_text),
                "reference": _merged_reference(selected),
                "source_references": references,
                "interpretation": "uncertain",
            }
        )
        selected_ids = {c.id for c in selected}
        cells = [c for c in cells if c.id not in selected_ids] + [merged]
    elif isinstance(operation, SetHeaders):
        structural = True
        updates = {
            "header_rows": tuple(sorted(operation.header_rows)),
            "row_label_columns": tuple(sorted(operation.row_label_columns)),
        }
        cells = [
            c.model_copy(
                update={
                    "role": "column_header"
                    if c.row in operation.header_rows
                    else "row_header"
                    if c.col in operation.row_label_columns
                    else "data"
                }
            )
            for c in cells
        ]
    elif isinstance(operation, SetNotes):
        updates = {"unit_notes": operation.unit_notes}
    else:
        raise TypeError("Unknown edit operation")
    cells.sort(key=lambda c: (c.row, c.col, c.id))
    result = content.model_copy(update={**updates, "cells": tuple(cells)})
    validate_grid(result)
    before, after = {c.id: c for c in content.cells}, {c.id: c for c in result.cells}
    affected = tuple(
        sorted(id for id in before.keys() | after.keys() if before.get(id) != after.get(id))
    )
    return EditResult(content=result, affected_cell_ids=affected, structural_change=structural)
