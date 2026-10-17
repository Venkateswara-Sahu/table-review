from uuid import uuid4

import pytest
from openpyxl import load_workbook

from table_review.exports.types import ExportInput
from table_review.exports.workbook import write_workbook
from tests.fixtures.tables import simple_table


@pytest.mark.parametrize(
    ("headers", "labels"),
    [
        ((0, 1, 9, 10, 18, 19), (0,)),
        ((9, 10), ()),
        ((0, 1), (0, 2)),
        ((0, 1), (0, 1, 2)),
        (tuple(range(27)), tuple(range(5))),
        (tuple(range(8)), ()),
        ((), (0,)),
    ],
)
def test_export_has_no_automatic_frozen_panes(tmp_path, headers, labels):
    content = simple_table(rows=27, columns=5).model_copy(
        update={"header_rows": headers, "row_label_columns": labels}
    )
    artifact = write_workbook(
        ExportInput(content=content, revision_id=uuid4()), tmp_path / "navigation.xlsx"
    )
    workbook = load_workbook(artifact.path)
    assert workbook.views[0].showVerticalScroll is not False
    for sheet in workbook:
        assert sheet.freeze_panes is None
    workbook.close()


def test_table_rows_ignore_off_table_commentary_when_sizing(tmp_path):
    content = simple_table().model_copy(update={"unit_notes": ("Long note " * 100,)})
    artifact = write_workbook(
        ExportInput(content=content, revision_id=uuid4()), tmp_path / "notes.xlsx"
    )
    workbook = load_workbook(artifact.path)
    sheet = workbook["Table"]
    assert sheet.row_dimensions[1].height <= 24
    assert sheet.row_dimensions[2].height <= 24
    assert sheet["A1"].value == "0:0"
    assert sheet["E4"].value == content.unit_notes[0]
    assert "$E$4" in sheet.print_area
    workbook.close()


def test_tall_header_does_not_consume_scrollable_viewport(tmp_path):
    content = simple_table().model_copy(update={"header_rows": (0,)})
    cells = list(content.cells)
    cells[0] = cells[0].model_copy(update={"text": "\n".join(["Long heading"] * 30)})
    content = content.model_copy(update={"cells": tuple(cells)})
    artifact = write_workbook(
        ExportInput(content=content, revision_id=uuid4()), tmp_path / "tall-heading.xlsx"
    )
    workbook = load_workbook(artifact.path)
    assert workbook["Table"].freeze_panes is None
    assert workbook["Table"]["A1"].value == cells[0].text
    workbook.close()
