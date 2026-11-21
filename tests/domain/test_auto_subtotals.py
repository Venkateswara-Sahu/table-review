from uuid import uuid4

from table_review.checks.engine import evaluate_checks
from table_review.checks.types import CheckContext
from table_review.documents.types import SourceReference
from table_review.tables.auto_subtotals import detect_subtotals
from table_review.tables.subtotal_types import SubtotalGroup, SubtotalTerm
from table_review.tables.types import CellContent, TableContent


def _cell(row, col, text, *, role="data", interp="en_us_number"):
    return CellContent(
        id=f"r{row}c{col}",
        row=row,
        col=col,
        raw_text=text,
        text=text,
        role=role,
        interpretation=interp,
        reference=SourceReference(page=1, precision="page"),
    )


def _table(grid_rows, *, header_rows=(0,), label_cols=(0,)):
    cells = []
    for r, row_vals in enumerate(grid_rows):
        for c, val in enumerate(row_vals):
            text, role, interp = val if isinstance(val, tuple) else (val, "data", "en_us_number")
            if r in header_rows:
                role = "column_header"
            elif c in label_cols and role == "data":
                role = "row_header"
            cells.append(_cell(r, c, text, role=role, interp=interp))
    return TableContent(
        rows=len(grid_rows),
        columns=len(grid_rows[0]),
        cells=tuple(cells),
        header_rows=header_rows,
        row_label_columns=label_cols,
        source_reference=SourceReference(page=1, precision="page"),
    )


def test_vertical_subtotals_grand_total_and_currency_prefix():
    content = _table(
        [
            ["Line item", "2025", "% change"],
            [("Revenue", "section", "text"), "", ""],
            ["Product sales", "$1,000", "10%"],
            ["Service sales", "500", "5%"],
            ["Total revenue", "$1,500", "8%"],
            [("Operating expenses", "section", "text"), "", ""],
            ["R&D", "(200)", "2%"],
            ["SG&A", "(300)", "3%"],
            ["Total operating expenses", "(500)", "2%"],
            ["Operating income", "$1,000", "15%"],
            ["Earnings per share", "2.50", "10%"],
        ]
    )
    detected = detect_subtotals(content)
    by_total = {item.total_cell_id: item for item in detected}
    assert set(by_total) == {"r4c1", "r8c1", "r9c1"}
    assert by_total["r4c1"].state == "match"
    assert by_total["r4c1"].term_cell_ids == ("r2c1", "r3c1")
    assert by_total["r8c1"].state == "match"
    assert by_total["r8c1"].term_cell_ids == ("r6c1", "r7c1")
    # Grand total sums the prior two subtotal rows!
    assert by_total["r9c1"].state == "match"
    assert by_total["r9c1"].term_cell_ids == ("r4c1", "r8c1")


def test_horizontal_cross_footing_and_rounding_match():
    content = _table(
        [
            ["Segment", "Q1", "Q2", "Q3", "Total"],
            ["North", "10.1", "10.1", "10.1", "30.4"],  # 30.3 vs 30.4 -> 0.1 rounding step across 3 items
            ["South", "20.0", "15.0", "5.0", "40.0"],
            ["Total", "30.1", "25.1", "15.1", "70.3"],
        ]
    )
    detected = detect_subtotals(content)
    by_id = {item.id: item for item in detected}
    assert by_id["auto:h:r1c4"].state == "rounding_match"
    assert by_id["auto:h:r2c4"].state == "match"
    assert by_id["auto:v:r3c1"].state == "match"
    assert by_id["auto:v:r3c4"].state == "difference"
    assert by_id["auto:v:r3c4"].difference == "-0.1"


def test_detected_mismatch_emits_check_issue_and_skips_declared_totals():
    content = _table(
        [
            ["Metric", "2025"],
            ["A", "100"],
            ["B", "200"],
            ["Total", "350"],
        ]
    )
    ctx = CheckContext(source_tokens=(), source_regions=(), source_available=False)
    issues = evaluate_checks(content, ctx)
    diff_issues = [i for i in issues if i.kind == "detected_subtotal_difference"]
    assert len(diff_issues) == 1
    assert diff_issues[0].evidence["difference"] == "50"
    assert set(diff_issues[0].affected_cell_ids) == {"r1c1", "r2c1", "r3c1"}

    # Once user explicitly declares that total, auto-detection skips duplicating it.
    declared = SubtotalGroup(
        id=uuid4(),
        name="Total",
        total_cell_id="r3c1",
        terms=(SubtotalTerm(cell_id="r1c1", sign=1), SubtotalTerm(cell_id="r2c1", sign=1)),
    )
    with_declared = content.model_copy(update={"subtotal_groups": (declared,)})
    assert detect_subtotals(with_declared) == ()


def test_detects_running_subtotal_income_statement_pattern():
    content = _table(
        [
            ["Line item", "FY 2025"],
            ["Product revenue", "$842.60"],
            ["Subscription services", "418.90"],
            ["Total revenue", "1,261.50"],
            ["Cost of revenue", "(438.20)"],
            ["Research & development", "(214.50)"],
            ["Sales & general admin", "(186.30)"],
            ["Operating income", "422.50"],
        ]
    )
    detected = detect_subtotals(content)
    by_id = {item.id: item for item in detected}
    assert by_id["auto:v:r3c1"].state == "match"
    assert by_id["auto:v:r3c1"].term_cell_ids == ("r1c1", "r2c1")
    assert by_id["auto:v:r7c1"].state == "match"
    assert by_id["auto:v:r7c1"].term_cell_ids == ("r3c1", "r4c1", "r5c1", "r6c1")
    assert by_id["auto:v:r7c1"].input_sum == "422.50"
