import pytest

from table_review.documents.types import PageBox, SourceReference
from table_review.tables.stitch import stitch_tables
from table_review.tables.types import CellContent, TableContent


def _page_table(
    page_no: int,
    rows_data: list[list[str]],
    *,
    header_rows: tuple[int, ...] = (0,),
    title: str | None = None,
    unit_notes: tuple[str, ...] = (),
) -> TableContent:
    ncols = len(rows_data[0])
    cells = []
    for r_idx, row in enumerate(rows_data):
        for c_idx, text in enumerate(row):
            box = PageBox(
                page=page_no,
                left=40.0 + c_idx * 100.0,
                top=80.0 + r_idx * 24.0,
                right=120.0 + c_idx * 100.0,
                bottom=98.0 + r_idx * 24.0,
                page_width=612.0,
                page_height=792.0,
            )
            role = (
                "column_header"
                if r_idx in header_rows
                else "row_header"
                if c_idx == 0
                else "data"
            )
            cells.append(
                CellContent(
                    id=f"c_{r_idx}_{c_idx}",
                    row=r_idx,
                    col=c_idx,
                    raw_text=text,
                    text=text,
                    role=role,
                    interpretation="text" if c_idx == 0 or r_idx in header_rows else "en_us_number",
                    reference=SourceReference(page=page_no, precision="cell", box=box),
                )
            )
    return TableContent(
        rows=len(rows_data),
        columns=ncols,
        cells=tuple(cells),
        header_rows=header_rows,
        row_label_columns=(0,),
        unit_notes=unit_notes,
        title=title,
        source_reference=SourceReference(
            page=page_no,
            precision="table",
            box=PageBox(
                page=page_no,
                left=40.0,
                top=80.0,
                right=40.0 + ncols * 100.0,
                bottom=80.0 + len(rows_data) * 24.0,
                page_width=612.0,
                page_height=792.0,
            ),
        ),
    )


def test_stitch_tables_drops_repeated_headers_pads_columns_and_keeps_provenance():
    t1 = _page_table(
        10,
        [
            ["Item", "2025", "2024"],
            ["Product revenue", "$500", "$450"],
            ["Service revenue", "$200", "$150"],
        ],
        title="Consolidated Income",
        unit_notes=("$ millions",),
    )
    t2 = _page_table(
        11,
        [
            ["Item", "2025", "2024"],
            ["Cost of revenue", "(300)", "(260)"],
        ],
        unit_notes=("$ millions", "Audited"),
    )
    # Narrower continuation page (2 columns instead of 3) still pads cleanly.
    t3 = _page_table(
        12,
        [
            ["Item", "2025"],
            ["Net income", "$400"],
        ],
        header_rows=(0,),
    )

    stitched = stitch_tables([t1, t2, t3])
    assert stitched.title == "Consolidated Income"
    assert (stitched.rows, stitched.columns) == (5, 3)
    assert stitched.header_rows == (0,)
    assert stitched.row_label_columns == (0,)
    assert stitched.unit_notes == ("$ millions", "Audited")
    assert len({cell.id for cell in stitched.cells}) == len(stitched.cells)

    by_slot = {(c.row, c.col): c for c in stitched.cells}
    assert by_slot[(0, 0)].text == "Item" and by_slot[(0, 0)].reference.page == 10
    assert by_slot[(1, 1)].text == "$500" and by_slot[(1, 1)].reference.page == 10
    assert by_slot[(3, 0)].text == "Cost of revenue" and by_slot[(3, 0)].reference.page == 11
    assert by_slot[(3, 1)].text == "(300)" and by_slot[(3, 1)].reference.box.page == 11
    assert by_slot[(4, 0)].text == "Net income" and by_slot[(4, 0)].reference.page == 12
    assert by_slot[(4, 2)].text == "" and by_slot[(4, 2)].reference.page == 12


def test_stitch_tables_keeps_headers_when_requested_and_requires_two_tables():
    t1 = _page_table(1, [["Line", "2025"], ["A", "10"]])
    t2 = _page_table(2, [["Line", "2025"], ["B", "20"]])

    kept = stitch_tables([t1, t2], title="Full Schedule", drop_repeated_headers=False)
    assert kept.title == "Full Schedule"
    assert kept.rows == 4
    by_slot = {(c.row, c.col): c for c in kept.cells}
    assert by_slot[(2, 0)].text == "Line" and by_slot[(2, 0)].reference.page == 2

    with pytest.raises(ValueError, match="at least two"):
        stitch_tables([t1])
