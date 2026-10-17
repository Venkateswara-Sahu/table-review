from types import SimpleNamespace

import pytest

from tests.domain.test_conversion_outcomes import extract_with


def parser_table():
    region = SimpleNamespace(l=30, t=100, r=400, b=220)
    region.to_top_left_origin = lambda height: region

    def cell(row, col, text, span=1):
        return SimpleNamespace(
            bbox=None,
            column_header=False,
            row_header=False,
            row_section=False,
            start_row_offset_idx=row,
            start_col_offset_idx=col,
            row_span=1,
            col_span=span,
            text=text,
        )

    return SimpleNamespace(
        prov=[SimpleNamespace(page_no=2, bbox=region)],
        data=SimpleNamespace(
            num_rows=2,
            num_cols=3,
            table_cells=[cell(0, 0, "ASSETS", 2), cell(1, 0, "Cash"), cell(1, 1, "123.45")],
        ),
    )


def empty_ocr_tokens(page, result):
    result.pages = []
    return result


@pytest.mark.parametrize("ocr", [False, True])
def test_missing_positions_are_editable_uncertain_drafts_without_splitting_parser_spans(
    tmp_path, monkeypatch, ocr
):
    result = extract_with(
        tmp_path,
        monkeypatch,
        {2: "success"},
        (parser_table(),),
        ocr_pages=(2,) if ocr else (),
        transform=empty_ocr_tokens,
    )
    table = result.tables[0]
    assert len(table.content.cells) == 5
    assert not any(c.row == 0 and c.col == 1 for c in table.content.cells)
    missing = [c for c in table.content.cells if c.col == 2]
    assert len(missing) == 2
    assert all(c.text == c.raw_text == "" and c.interpretation == "uncertain" for c in missing)
    assert all(
        c.reference.precision == "table" and c.reference.box == table.region for c in missing
    )
    assert all(not c.source_references for c in missing)
    assert any(
        "omitted 2 table positions" in w and "manual image review" in w for w in result.warnings
    )
    assert next(c for c in table.content.cells if c.text == "ASSETS").col_span == 2
    assert next(c for c in table.content.cells if c.text == "123.45").raw_text == "123.45"


def test_native_placeholder_ids_are_stable_across_identical_extraction_requests(
    tmp_path, monkeypatch
):
    results = []
    for name in ("first", "second"):
        directory = tmp_path / name
        directory.mkdir()
        results.append(extract_with(directory, monkeypatch, {2: "success"}, (parser_table(),)))
    first, second = (r.tables[0].content for r in results)
    assert {(c.row, c.col): c.id for c in first.cells} == {
        (c.row, c.col): c.id for c in second.cells
    }
    assert len(first.cells) == len(second.cells) == 5
