"""Clean TSV, Markdown, and JSON table serialization for analyst copy-paste."""

import re

from table_review.tables.types import CellContent, TableContent

_WS_RUN = re.compile(r"[\t\r\n]+")


def _clean_cell_text(text: str) -> str:
    return _WS_RUN.sub(" ", text).strip()


def _grid_matrix(content: TableContent) -> list[list[str]]:
    matrix = [[""] * content.columns for _ in range(content.rows)]
    for cell in content.cells:
        if 0 <= cell.row < content.rows and 0 <= cell.col < content.columns:
            matrix[cell.row][cell.col] = _clean_cell_text(cell.text)
    return matrix


def format_table_tsv(content: TableContent) -> str:
    """Render rectangular tab-separated text ready to paste into Excel or Sheets."""
    return "\n".join("\t".join(row) for row in _grid_matrix(content))


def _column_labels(content: TableContent, matrix: list[list[str]]) -> tuple[list[str], int]:
    header_rows = sorted(r for r in content.header_rows if 0 <= r < content.rows)
    if header_rows:
        # Build composite column headers from the leading contiguous header band.
        band = [header_rows[0]]
        for r in header_rows[1:]:
            if r == band[-1] + 1:
                band.append(r)
            else:
                break
        by_pos: dict[tuple[int, int], CellContent] = {}
        for cell in content.cells:
            for r in range(cell.row, cell.row + cell.row_span):
                for c in range(cell.col, cell.col + cell.col_span):
                    by_pos[(r, c)] = cell
        raw_labels = []
        for col in range(content.columns):
            parts: list[str] = []
            seen_ids: set[str] = set()
            for r in band:
                cell = by_pos.get((r, col))
                if cell and cell.id not in seen_ids:
                    seen_ids.add(cell.id)
                    cleaned = _clean_cell_text(cell.text)
                    if cleaned:
                        parts.append(cleaned)
            raw_labels.append(" / ".join(parts) if parts else f"Column {col + 1}")
        body_start = band[-1] + 1
    else:
        raw_labels = [text or f"Column {col + 1}" for col, text in enumerate(matrix[0])]
        body_start = 1 if content.rows > 1 else 0

    counts: dict[str, int] = {}
    unique: list[str] = []
    for label in raw_labels:
        counts[label] = counts.get(label, 0) + 1
        unique.append(label if counts[label] == 1 else f"{label} ({counts[label]})")
    return unique, body_start


def _escape_md(val: str) -> str:
    return val.replace("|", "\\|")


def format_table_markdown(content: TableContent) -> str:
    """Render a GFM Markdown table with composite headers."""
    matrix = _grid_matrix(content)
    headers, body_start = _column_labels(content, matrix)
    lines = [
        "| " + " | ".join(_escape_md(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    header_set = set(content.header_rows)
    for row_idx in range(body_start, content.rows):
        if row_idx in header_set and content.header_rows:
            continue
        lines.append("| " + " | ".join(_escape_md(cell) for cell in matrix[row_idx]) + " |")
    return "\n".join(lines)


def format_table_json(content: TableContent) -> dict:
    """Render structured row records keyed by column headers with table metadata."""
    matrix = _grid_matrix(content)
    headers, body_start = _column_labels(content, matrix)
    header_set = set(content.header_rows)
    rows = []
    for row_idx in range(body_start, content.rows):
        if row_idx in header_set and content.header_rows:
            continue
        rows.append({headers[col]: matrix[row_idx][col] for col in range(content.columns)})
    return {
        "title": content.title,
        "columns": headers,
        "rows": rows,
        "shape": {"rows": content.rows, "columns": content.columns},
        "table_context": content.table_context.model_dump(mode="json")
        if content.table_context
        else None,
        "unit_notes": list(content.unit_notes),
    }
