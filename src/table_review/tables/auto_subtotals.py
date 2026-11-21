"""Heuristic detection of vertical subtotals, grand totals, and horizontal cross-footing."""

import re
from decimal import Decimal, localcontext

from table_review.tables.numbers import interpret_value
from table_review.tables.subtotal_types import DetectedSubtotal
from table_review.tables.types import CellContent, TableContent

_TOTAL_ROW_RE = re.compile(
    r"^(?:total\b|subtotal\b|sub-total\b|"
    r"net\s+(?:income|loss|revenue|sales|cash|operating|profit|assets|liabilities|earnings)\b|"
    r"gross\s+(?:profit|margin)\b|"
    r"operating\s+(?:income|loss|profit)\b|"
    r"income\s+before\b|profit\s+before\b|ebitda\b|"
    r"balance\s+(?:at|as\s+of)\s+end\b|"
    r"cash\s+and\s+cash\s+equivalents\s+at\s+end\b)",
    re.IGNORECASE,
)

_EXCLUDE_ROW_RE = re.compile(
    r"\b(?:per\s+share|percentage|ratio|margin\s*%|weighted[\s-]average|shares?\s+outstanding|basic|diluted|eps)\b|%",
    re.IGNORECASE,
)

_EXCLUDE_COL_HEADER_RE = re.compile(
    r"%|\b(?:percent(?:age)?|change|bps|basis\s+points?|ratio|per[ -]share|shares|margin)\b",
    re.IGNORECASE,
)

_TOTAL_COL_HEADER_RE = re.compile(r"^(?:total|consolidated|combined)\b", re.IGNORECASE)


def _parse_numeric(cell: CellContent) -> Decimal | None:
    if cell.role not in ("data", "value") or cell.row_span != 1 or cell.col_span != 1:
        return None
    if len(cell.text) > 128 or cell.interpretation in ("text", "percent"):
        return None
    mode = "en_us_number" if cell.interpretation == "uncertain" else cell.interpretation
    parsed = interpret_value(cell.text, cell.role, mode)
    if parsed.kind != "number" or parsed.decimal_value is None:
        return None
    if parsed.decimal_value.as_tuple().exponent < -12 or parsed.decimal_value.adjusted() > 14:
        return None
    return parsed.decimal_value


def _classify_difference(
    reported: Decimal, terms: list[tuple[CellContent, Decimal]]
) -> tuple[Decimal, Decimal, str]:
    with localcontext() as ctx:
        ctx.prec = 64
        input_sum = sum((val for _, val in terms), Decimal(0))
        difference = reported - input_sum
    if difference == 0:
        return input_sum, difference, "match"
    min_exp = min(
        (
            val.as_tuple().exponent
            for _, val in [*terms, (None, reported)]
            if isinstance(val.as_tuple().exponent, int)
        ),
        default=0,
    )
    rounding_step = Decimal(10) ** min_exp
    if len(terms) >= 3 and abs(difference) == rounding_step:
        return input_sum, difference, "rounding_match"
    return input_sum, difference, "difference"


def detect_subtotals(
    content: TableContent, *, unreadable_ids: tuple[str, ...] | set[str] = ()
) -> tuple[DetectedSubtotal, ...]:
    """Identify candidate vertical and horizontal subtotals without guessing ambiguous rows."""
    unreadable = set(unreadable_ids)
    header_rows = set(content.header_rows)
    label_cols = tuple(sorted(content.row_label_columns)) if content.row_label_columns else (0,)
    label_col_set = set(label_cols)
    declared_totals = {group.total_cell_id for group in content.subtotal_groups}

    grid: dict[tuple[int, int], CellContent] = {}
    for cell in content.cells:
        grid[(cell.row, cell.col)] = cell

    def row_label(row: int) -> str:
        parts = []
        for col in label_cols:
            cell = grid.get((row, col))
            if cell and cell.text.strip():
                parts.append(cell.text.strip())
        return " ".join(parts)

    def col_headers(col: int) -> list[str]:
        texts = []
        for cell in content.cells:
            if cell.row in header_rows and cell.col <= col < cell.col + cell.col_span and cell.text.strip():
                texts.append(cell.text.strip())
        return texts

    def is_excluded_col(col: int) -> bool:
        if col in label_col_set:
            return True
        if any(_EXCLUDE_COL_HEADER_RE.search(text) for text in col_headers(col)):
            return True
        body_cells = [
            grid[(row, col)]
            for row in range(content.rows)
            if row not in header_rows and (row, col) in grid and grid[(row, col)].text.strip()
        ]
        if not body_cells:
            return True
        percent_count = sum(
            interpret_value(c.text, c.role, "percent").kind == "percent"
            or c.interpretation == "percent"
            for c in body_cells
        )
        return percent_count * 2 > len(body_cells)

    excluded_cols = {col for col in range(content.columns) if is_excluded_col(col)}

    # Pre-classify rows: section breaks and candidate total rows.
    section_rows: set[int] = set()
    candidate_total_rows: list[tuple[int, str]] = []
    for row in range(content.rows):
        if row in header_rows:
            continue
        label = row_label(row)
        row_cells = [grid[(row, c)] for c in range(content.columns) if (row, c) in grid]
        has_section_role = any(c.role == "section" for c in row_cells)
        has_any_numeric = any(
            c.col not in label_col_set and _parse_numeric(c) is not None for c in row_cells
        )
        if has_section_role or (label and not has_any_numeric):
            section_rows.add(row)
        if label and _TOTAL_ROW_RE.search(label) and not _EXCLUDE_ROW_RE.search(label):
            candidate_total_rows.append((row, label))

    candidate_row_set = {row for row, _ in candidate_total_rows}
    detected: list[DetectedSubtotal] = []
    detected_rows_by_col: dict[int, list[int]] = {}

    for total_row, label in candidate_total_rows:
        for col in range(content.columns):
            if col in excluded_cols:
                continue
            total_cell = grid.get((total_row, col))
            if (
                total_cell is None
                or total_cell.id in declared_totals
                or total_cell.id in unreadable
            ):
                continue
            reported = _parse_numeric(total_cell)
            if reported is None:
                continue

            # Scan upward from total_row - 1 until hitting a header row, section break,
            # or an earlier subtotal row.
            terms: list[tuple[CellContent, Decimal]] = []
            for r in range(total_row - 1, -1, -1):
                if r in header_rows or r in section_rows or r in candidate_row_set:
                    break
                cell = grid.get((r, col))
                if cell is None or cell.id in unreadable:
                    continue
                val = _parse_numeric(cell)
                if val is not None:
                    terms.append((cell, val))
            terms.reverse()

            prior_subtotals = [
                (grid[(r, col)], _parse_numeric(grid[(r, col)]))
                for r in detected_rows_by_col.get(col, [])
                if (r, col) in grid and grid[(r, col)].id not in unreadable
            ]
            prior_subtotals = [(c, v) for c, v in prior_subtotals if v is not None]

            chosen_terms = terms
            if len(chosen_terms) >= 2:
                input_sum, difference, state = _classify_difference(reported, chosen_terms)
                # Running-subtotal fallback (e.g. Total revenue - Operating expenses = Operating income).
                if state == "difference" and prior_subtotals and terms:
                    running_terms = [prior_subtotals[-1], *terms]
                    run_sum, run_diff, run_state = _classify_difference(reported, running_terms)
                    if run_state != "difference":
                        chosen_terms = running_terms
                        input_sum, difference, state = run_sum, run_diff, run_state
                # Grand-total fallback: if contiguous terms don't match, try summing prior subtotals.
                if state == "difference" and len(prior_subtotals) >= 2:
                    alt_sum, alt_diff, alt_state = _classify_difference(reported, prior_subtotals)
                    if alt_state != "difference" or abs(alt_diff) < abs(difference):
                        chosen_terms = prior_subtotals
                        input_sum, difference, state = alt_sum, alt_diff, alt_state
            elif prior_subtotals and terms:
                running_terms = [prior_subtotals[-1], *terms]
                run_sum, run_diff, run_state = _classify_difference(reported, running_terms)
                if run_state != "difference":
                    chosen_terms = running_terms
                    input_sum, difference, state = run_sum, run_diff, run_state
                elif len(prior_subtotals) >= 2:
                    chosen_terms = prior_subtotals
                    input_sum, difference, state = _classify_difference(reported, chosen_terms)
                else:
                    continue
            elif len(prior_subtotals) >= 2:
                chosen_terms = prior_subtotals
                input_sum, difference, state = _classify_difference(reported, chosen_terms)
            else:
                continue

            detected.append(
                DetectedSubtotal(
                    id=f"auto:v:{total_cell.id}",
                    direction="vertical",
                    label=label,
                    total_cell_id=total_cell.id,
                    term_cell_ids=tuple(c.id for c, _ in chosen_terms),
                    input_sum=format(input_sum, "f"),
                    reported_total=format(reported, "f"),
                    difference=format(difference, "f"),
                    state=state,
                )
            )
            detected_rows_by_col.setdefault(col, []).append(total_row)

    # Horizontal cross-footing when the rightmost column is a Total / Consolidated column.
    last_col = content.columns - 1
    if last_col >= 2 and last_col not in excluded_cols:
        right_headers = col_headers(last_col)
        if any(_TOTAL_COL_HEADER_RE.search(text) for text in right_headers):
            for row in range(content.rows):
                if row in header_rows:
                    continue
                total_cell = grid.get((row, last_col))
                if (
                    total_cell is None
                    or total_cell.id in declared_totals
                    or total_cell.id in unreadable
                ):
                    continue
                reported = _parse_numeric(total_cell)
                if reported is None:
                    continue
                h_terms: list[tuple[CellContent, Decimal]] = []
                for col in range(last_col):
                    if col in excluded_cols:
                        continue
                    cell = grid.get((row, col))
                    if cell is None or cell.id in unreadable:
                        continue
                    val = _parse_numeric(cell)
                    if val is not None:
                        h_terms.append((cell, val))
                if len(h_terms) < 2:
                    continue
                input_sum, difference, state = _classify_difference(reported, h_terms)
                label = row_label(row) or f"Row {row + 1}"
                detected.append(
                    DetectedSubtotal(
                        id=f"auto:h:{total_cell.id}",
                        direction="horizontal",
                        label=f"{label} (row total)",
                        total_cell_id=total_cell.id,
                        term_cell_ids=tuple(c.id for c, _ in h_terms),
                        input_sum=format(input_sum, "f"),
                        reported_total=format(reported, "f"),
                        difference=format(difference, "f"),
                        state=state,
                    )
                )

    return tuple(detected)
