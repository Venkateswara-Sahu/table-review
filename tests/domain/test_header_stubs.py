from table_review.documents.types import SourceReference
from table_review.tables.types import CellContent, TableContent


def test_aligned_unique_text_identifiers_become_row_labels_without_changing_literals():
    from table_review.extraction.structure import infer_aligned_row_labels

    ref = SourceReference(page=1, precision="page")
    values = (("Person", "Age", "Position"), ("Ada", "30", "Engineer"), ("Grace", "40", "Lead"))
    original = TableContent(
        rows=3,
        columns=3,
        header_rows=(0,),
        cells=tuple(
            CellContent(
                id=f"{row}:{col}",
                row=row,
                col=col,
                text=value,
                raw_text=value,
                role="column_header" if row == 0 else "data",
                reference=ref,
            )
            for row, line in enumerate(values)
            for col, value in enumerate(line)
        ),
    )
    updated, changes = infer_aligned_row_labels(original)
    assert updated.row_label_columns == (0,)
    assert [c.role for c in updated.cells if c.col == 0 and c.row > 0] == [
        "row_header",
        "row_header",
    ]
    assert [(c.id, c.text, c.raw_text, c.reference) for c in updated.cells] == [
        (c.id, c.text, c.raw_text, c.reference) for c in original.cells
    ]
    assert changes
    numeric = original.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"text": "101", "raw_text": "101"})
                if c.col == 0 and c.row > 0
                else c
                for c in original.cells
            )
        }
    )
    assert infer_aligned_row_labels(numeric) == (numeric, ())


def content():
    ref = SourceReference(page=1, precision="page")
    return TableContent(
        rows=3,
        columns=2,
        header_rows=(0, 1),
        row_label_columns=(0,),
        cells=(
            CellContent(
                id="blank0",
                row=0,
                col=0,
                text="",
                raw_text="",
                interpretation="uncertain",
                reference=ref,
            ),
            CellContent(
                id="blank1",
                row=1,
                col=0,
                text="",
                raw_text="",
                interpretation="uncertain",
                reference=ref,
            ),
            CellContent(
                id="header0",
                row=0,
                col=1,
                text="Year",
                raw_text="Year",
                role="column_header",
                reference=ref,
            ),
            CellContent(
                id="header1",
                row=1,
                col=1,
                text="2026",
                raw_text="2026",
                role="column_header",
                reference=ref,
            ),
            CellContent(
                id="label",
                row=2,
                col=0,
                text="Revenue",
                raw_text="Revenue",
                role="row_header",
                reference=ref,
            ),
            CellContent(id="value", row=2, col=1, text="10", raw_text="10", reference=ref),
        ),
    )


def test_omitted_empty_header_stub_is_one_uncertain_span_without_changing_recognized_cells():
    from table_review.extraction.structure import normalize_header_stubs

    original = content()
    updated, changes = normalize_header_stubs(original)
    assert len(updated.cells) == 5
    blank = next(c for c in updated.cells if c.id == "blank0")
    assert (
        blank.row_span == 2
        and blank.interpretation == "uncertain"
        and blank.text == blank.raw_text == ""
    )
    assert [c for c in updated.cells if c.text] == [c for c in original.cells if c.text]
    assert next(c for c in changes if c["reason"] == "omitted_empty_header_stub")[
        "removed_empty_cell_ids"
    ] == ["blank1"]


def test_header_stub_with_recognized_text_is_not_merged():
    from table_review.extraction.structure import normalize_header_stubs

    original = content()
    cells = tuple(
        c.model_copy(update={"text": "Label", "raw_text": "Label"}) if c.id == "blank1" else c
        for c in original.cells
    )
    original = original.model_copy(update={"cells": cells})
    updated, changes = normalize_header_stubs(original)
    assert len(updated.cells) == len(original.cells)
    assert all(c.row_span == 1 for c in updated.cells)
    assert not any(c["reason"] == "omitted_empty_header_stub" for c in changes)


def test_unrelated_data_blanks_are_not_combined():
    from table_review.extraction.structure import normalize_header_stubs

    original = content().model_copy(update={"header_rows": ()})
    updated, changes = normalize_header_stubs(original)
    assert updated == original and not changes


def test_row_label_header_role_follows_the_explicit_header_band():
    from table_review.extraction.structure import normalize_header_stubs

    original = content().model_copy(update={"header_rows": (0,)})
    cells = tuple(
        c.model_copy(update={"text": "Year ended", "raw_text": "Year ended"})
        if c.id == "blank0"
        else c
        for c in original.cells
    )
    updated, changes = normalize_header_stubs(original.model_copy(update={"cells": cells}))
    assert updated.cells[0].role == "column_header"
    assert updated.cells[0].raw_text == updated.cells[0].text == "Year ended"
    assert any(c["reason"] == "row_label_header_role" for c in changes)
