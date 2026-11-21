"""Multi-page table stitching preserving exact cell-level PDF page provenance."""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from table_review.documents.types import SourceReference
from table_review.tables.grid import validate_grid
from table_review.tables.types import CellContent, TableContent


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def _header_row_signatures(content: TableContent) -> list[list[str]]:
    if not content.header_rows:
        return []
    max_col = content.columns
    rows_by_idx: dict[int, list[str]] = {}
    for row_idx in sorted(content.header_rows):
        rows_by_idx[row_idx] = [""] * max_col
    for cell in content.cells:
        if cell.row in rows_by_idx:
            text = _normalize_text(cell.text)
            for c in range(cell.col, min(max_col, cell.col + cell.col_span)):
                rows_by_idx[cell.row][c] = text
    return [rows_by_idx[r] for r in sorted(rows_by_idx)]


def _rows_match_headers(
    part: TableContent, first_headers: list[list[str]]
) -> int:
    """Count leading contiguous rows at top of `part` repeating `first_headers`."""
    if not first_headers or part.rows <= 1:
        return 0
    candidate_indices = (
        sorted(part.header_rows)
        if part.header_rows
        else list(range(min(len(first_headers), part.rows)))
    )
    if candidate_indices != list(range(len(candidate_indices))):
        return 0
    max_drop = min(len(first_headers), len(candidate_indices), part.rows - 1)
    if max_drop <= 0:
        return 0

    part_rows: dict[int, list[str]] = {r: [""] * part.columns for r in range(max_drop)}
    for cell in part.cells:
        for r in range(cell.row, min(max_drop, cell.row + cell.row_span)):
            text = _normalize_text(cell.text)
            for c in range(cell.col, min(part.columns, cell.col + cell.col_span)):
                part_rows[r][c] = text

    matched_rows = 0
    for r in range(max_drop):
        expected = first_headers[r]
        actual = part_rows[r]
        compared = 0
        hits = 0
        for col_idx in range(min(len(expected), len(actual))):
            if not expected[col_idx] and not actual[col_idx]:
                continue
            compared += 1
            if expected[col_idx] == actual[col_idx]:
                hits += 1
        if compared == 0 or hits == 0 or (hits / compared) < 0.75:
            break
        matched_rows += 1
    return matched_rows


def _safe_cell_id(part_idx: int, cell_id: str, used: set[str]) -> str:
    candidate = f"p{part_idx}_{cell_id}"
    if len(candidate) <= 64 and candidate not in used:
        used.add(candidate)
        return candidate
    hashed = str(uuid5(NAMESPACE_URL, f"stitch:{part_idx}:{cell_id}"))
    used.add(hashed)
    return hashed


def _fallback_reference(part: TableContent) -> SourceReference:
    if part.source_reference is not None:
        return part.source_reference
    if part.cells:
        return SourceReference(page=part.cells[0].reference.page, precision="page")
    return SourceReference(page=1, precision="page")


def stitch_tables(
    tables: Sequence[TableContent],
    *,
    title: str | None = None,
    drop_repeated_headers: bool = True,
) -> TableContent:
    """Concatenate multi-page tables vertically while keeping original cell page coordinates."""
    if len(tables) < 2:
        raise ValueError("Select at least two tables to stitch")

    target_cols = max(t.columns for t in tables)
    first_headers = _header_row_signatures(tables[0]) if drop_repeated_headers else []

    combined_cells: list[CellContent] = []
    used_ids: set[str] = set()
    row_offset = 0

    for part_idx, part in enumerate(tables):
        dropped = _rows_match_headers(part, first_headers) if part_idx > 0 else 0
        surviving_rows = part.rows - dropped
        if surviving_rows < 1:
            raise ValueError("Continuation table contains no rows after header removal")

        part_fallback_ref = _fallback_reference(part)
        row_fallback_ref: dict[int, SourceReference] = {}
        occupied_in_part: set[tuple[int, int]] = set()

        for cell in sorted(part.cells, key=lambda c: (c.row, c.col)):
            if cell.row < dropped:
                if cell.row + cell.row_span <= dropped:
                    continue
                new_local_row = 0
                new_row_span = cell.row + cell.row_span - dropped
            else:
                new_local_row = cell.row - dropped
                new_row_span = cell.row_span
            row_fallback_ref.setdefault(
                new_local_row,
                SourceReference(page=cell.reference.page, precision="page"),
            )
            new_row = row_offset + new_local_row
            for rr in range(new_local_row, new_local_row + new_row_span):
                for cc in range(cell.col, cell.col + cell.col_span):
                    occupied_in_part.add((rr, cc))
            combined_cells.append(
                cell.model_copy(
                    update={
                        "id": _safe_cell_id(part_idx, cell.id, used_ids),
                        "row": new_row,
                        "row_span": new_row_span,
                    }
                )
            )

        for local_r in range(surviving_rows):
            r_ref = row_fallback_ref.get(local_r, part_fallback_ref)
            for col in range(target_cols):
                if (local_r, col) in occupied_in_part:
                    continue
                combined_cells.append(
                    CellContent(
                        id=_safe_cell_id(part_idx, f"pad_{local_r}_{col}", used_ids),
                        row=row_offset + local_r,
                        col=col,
                        raw_text="",
                        text="",
                        role="row_header" if col in tables[0].row_label_columns else "data",
                        interpretation="uncertain",
                        reference=r_ref,
                    )
                )

        row_offset += surviving_rows

    resolved_title = (
        title.strip()[:200]
        if title is not None and title.strip()
        else next((t.title for t in tables if t.title and t.title.strip()), None)
    )
    unit_notes = tuple(
        dict.fromkeys(note for part in tables for note in part.unit_notes if note.strip())
    )
    seen_sources = {}
    for part in tables:
        for src in part.context_sources:
            seen_sources.setdefault(src.id, src)
    context_sources = tuple(seen_sources.values())[:32]
    table_context = next((t.table_context for t in tables if t.table_context is not None), None)

    header_rows = tuple(r for r in tables[0].header_rows if 0 <= r < row_offset)
    row_label_columns = tuple(c for c in tables[0].row_label_columns if 0 <= c < target_cols)

    result = TableContent(
        rows=row_offset,
        columns=target_cols,
        cells=tuple(combined_cells),
        header_rows=header_rows,
        row_label_columns=row_label_columns,
        unit_notes=unit_notes,
        source_reference=tables[0].source_reference or _fallback_reference(tables[0]),
        title=resolved_title,
        table_context=table_context,
        context_sources=context_sources,
    )
    validate_grid(result)
    return result
