import pytest
from PIL import Image, ImageDraw

from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.marks import dash_candidates
from table_review.tables.types import CellContent, TableContent


def fixture_content():
    region = PageBox(page=1, left=10, top=0, right=300, bottom=120, page_width=400, page_height=300)
    reference = SourceReference(page=1, precision="table", box=region)
    cells = []
    for row, top in enumerate((10, 50, 90)):
        for col, left in enumerate((20, 120, 210)):
            missing = (row, col) == (1, 2)
            box = PageBox(
                page=1,
                left=left,
                top=top,
                right=left + 30,
                bottom=top + 10,
                page_width=400,
                page_height=300,
            )
            cells.append(
                CellContent(
                    id=f"{row}:{col}",
                    row=row,
                    col=col,
                    text="" if missing else "100",
                    raw_text="" if missing else "100",
                    reference=reference
                    if missing
                    else SourceReference(page=1, precision="cell", box=box),
                )
            )
    return TableContent(rows=3, columns=3, cells=tuple(cells), source_reference=reference)


def test_isolated_dash_has_ink_geometry_without_inventing_a_literal():
    image = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(image).line((222, 55, 232, 55), fill="black", width=1)
    marks, inspected = dash_candidates(fixture_content(), image, 1)
    assert inspected == 1 and len(marks) == 1
    assert marks[0].cell_id == "1:2"
    assert (marks[0].box.left, marks[0].box.top, marks[0].box.right, marks[0].box.bottom) == (
        222,
        55,
        233,
        56,
    )


@pytest.mark.parametrize("kind", ["blank", "rule", "number"])
def test_blank_table_rule_and_number_are_not_isolated_marks(kind):
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    if kind == "rule":
        draw.line((208, 55, 242, 55), fill="black", width=1)
    elif kind == "number":
        draw.line((222, 55, 232, 55), fill="black", width=1)
        draw.rectangle((215, 50, 217, 59), fill="black")
    assert dash_candidates(fixture_content(), image, 1)[0] == ()


def test_wrong_raster_scale_cannot_publish_precise_mark_geometry():
    image = Image.new("RGB", (800, 600), "white")
    assert dash_candidates(fixture_content(), image, 1) == ((), 0)
