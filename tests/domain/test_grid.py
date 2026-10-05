import pytest

from tests.fixtures.tables import simple_table


def edit(table, **operation):
    from table_review.tables.grid import apply_edit, parse_edit

    return apply_edit(table, parse_edit(operation))


def test_text_edit_keeps_raw_reference_and_ids():
    table = simple_table()
    result = edit(table, kind="set_text", cell_id="r1c1", text="(1,234.50)")
    assert result.affected_cell_ids == ("r1c1",) and not result.structural_change
    cell = next(c for c in result.content.cells if c.id == "r1c1")
    assert cell.text == "(1,234.50)" and cell.raw_text == "1:1"
    assert cell.reference == table.cells[4].reference
    assert table.cells[4].text == "1:1"


@pytest.mark.parametrize("kind,axis", [("insert_row", "row"), ("insert_column", "col")])
@pytest.mark.parametrize("index", [0, 1, 3])
def test_boundary_insertion_preserves_existing_ids(kind, axis, index):
    table = simple_table()
    result = edit(table, kind=kind, index=index)
    assert result.structural_change
    old_ids = {c.id for c in table.cells}
    assert old_ids <= {c.id for c in result.content.cells}
    for cell in result.content.cells:
        if cell.id not in old_ids:
            assert cell.reference.precision == "page" and cell.reference.origin == "user"
            assert not cell.raw_text and not cell.text
        else:
            original = next(c for c in table.cells if c.id == cell.id)
            assert getattr(cell, axis) == getattr(original, axis) + (
                getattr(original, axis) >= index
            )


def test_spans_expand_and_contract_and_split_without_duplicate_values():
    merged = edit(simple_table(), kind="merge", row=0, col=0, rows=2, columns=2).content
    cell = next(c for c in merged.cells if c.id == "r0c0")
    assert (cell.row_span, cell.col_span) == (2, 2)
    # Merging keeps all distinct source text, with an explicit deterministic newline boundary.
    assert cell.text == "0:0\n0:1\n1:0\n1:1"
    inserted = edit(merged, kind="insert_row", index=1).content
    assert next(c for c in inserted.cells if c.id == cell.id).row_span == 3
    deleted = edit(inserted, kind="delete_row", index=1).content
    assert deleted == merged
    split = edit(deleted, kind="split", cell_id=cell.id).content
    assert len(split.cells) == 9
    assert next(c for c in split.cells if c.id == cell.id).text == cell.text
    assert sum(bool(c.text) for c in split.cells) == 6


def test_delete_start_of_span_keeps_content_and_id():
    merged = edit(simple_table(), kind="merge", row=0, col=0, rows=2, columns=2).content
    deleted = edit(merged, kind="delete_row", index=0).content
    cell = next(c for c in deleted.cells if c.id == "r0c0")
    assert cell.row == 0 and cell.row_span == 1 and cell.text.startswith("0:0")


def test_partial_span_merge_and_invalid_indices_rejected():
    merged = edit(simple_table(), kind="merge", row=0, col=0, rows=2, columns=2).content
    with pytest.raises(ValueError, match="span"):
        edit(merged, kind="merge", row=1, col=1, rows=2, columns=2)
    for operation in [
        {"kind": "insert_row", "index": 4},
        {"kind": "delete_column", "index": 3},
        {"kind": "set_text", "cell_id": "missing", "text": "x"},
    ]:
        with pytest.raises(ValueError):
            edit(simple_table(), **operation)
    with pytest.raises(ValueError):
        edit(simple_table(1, 1), kind="delete_row", index=0)


def test_invalid_topology_and_all_limits():
    from table_review.tables.grid import validate_grid

    table = simple_table()
    overlap = table.model_copy(
        update={"cells": table.cells + (table.cells[0].model_copy(update={"id": "extra"}),)}
    )
    with pytest.raises(ValueError, match="overlap"):
        validate_grid(overlap)
    outside = table.model_copy(
        update={"cells": (table.cells[0].model_copy(update={"row_span": 4}),)}
    )
    with pytest.raises(ValueError, match="outside"):
        validate_grid(outside)
    repeated = table.model_copy(
        update={
            "cells": (table.cells[0], table.cells[1].model_copy(update={"id": table.cells[0].id}))
        }
    )
    with pytest.raises(ValueError, match="ID"):
        validate_grid(repeated)
    for rows, columns, message in [
        (501, 1, "rows"),
        (1, 61, "columns"),
        (201, 50, "slots"),
        (137, 73, "slots"),
    ]:
        invalid = table.model_copy(update={"rows": rows, "columns": columns, "cells": ()})
        with pytest.raises(ValueError, match=message):
            validate_grid(invalid)


def test_header_notes_interpretation_and_checksum():
    from table_review.tables.types import TableContent, content_checksum

    table = simple_table()
    headers = edit(table, kind="set_headers", header_rows=[0], row_label_columns=[0]).content
    assert all(c.role == "column_header" for c in headers.cells if c.row == 0)
    assert all(c.role == "row_header" for c in headers.cells if c.row > 0 and c.col == 0)
    notes = edit(headers, kind="set_notes", unit_notes=["USD millions", "Raw scale only"]).content
    interpreted = edit(
        notes, kind="set_interpretation", cell_id="r1c1", interpretation="percent"
    ).content
    assert interpreted.cells[4].interpretation == "percent"
    assert content_checksum(table) != content_checksum(headers)
    assert content_checksum(notes) != content_checksum(interpreted)
    assert content_checksum(interpreted) == content_checksum(
        TableContent.model_validate_json(interpreted.model_dump_json())
    )
    with pytest.raises(ValueError):
        edit(table, kind="set_headers", header_rows=[3], row_label_columns=[])


def test_inserted_cell_cannot_claim_its_neighbors_source_box():
    from table_review.documents.types import PageBox, SourceReference

    table = simple_table(1, 1)
    box = PageBox(page=2, left=10, top=10, right=20, bottom=20, page_width=100, page_height=100)
    existing = table.cells[0].model_copy(
        update={"reference": SourceReference(page=2, precision="cell", box=box)}
    )
    result = edit(table.model_copy(update={"cells": (existing,)}), kind="insert_row", index=1)
    new = next(c for c in result.content.cells if c.id != existing.id)
    assert new.reference.precision == "page" and new.reference.box is None


def test_empty_manual_table_requires_explicit_original_page():
    from table_review.documents.types import SourceReference
    from table_review.tables.types import TableContent

    table = TableContent(
        rows=1,
        columns=1,
        cells=(),
        source_reference=SourceReference(page=5, precision="page", origin="user"),
    )
    result = edit(table, kind="insert_row", index=1)
    assert result.content.cells[0].reference.page == 5
    unknown = TableContent(rows=1, columns=1, cells=())
    with pytest.raises(ValueError, match="source page"):
        edit(unknown, kind="insert_row", index=1)


def test_legacy_serialized_interpretations_remain_readable():
    from table_review.tables.types import TableContent

    data = simple_table().model_dump()
    data["cells"][0]["interpretation"] = "auto"
    data["cells"][1]["interpretation"] = "number"
    table = TableContent.model_validate(data)
    assert table.cells[0].interpretation == table.cells[1].interpretation == "en_us_number"


def test_invalid_negative_span_geometry_and_reference_expansion():
    from table_review.tables.grid import expand_grid, validate_grid

    table = simple_table()
    for update in [{"row": -1}, {"col": -1}, {"row_span": 0}, {"col_span": 0}]:
        invalid = table.model_copy(update={"cells": (table.cells[0].model_copy(update=update),)})
        with pytest.raises(ValueError):
            validate_grid(invalid)
    merged = edit(table, kind="merge", row=0, col=0, rows=2, columns=2).content
    grid = expand_grid(merged)
    assert grid[0][0] is grid[1][1]


def test_header_metadata_uses_actual_roles_including_spans():
    from table_review.tables.types import infer_header_roles

    table = simple_table()
    cells = (
        table.cells[0].model_copy(update={"role": "column_header", "row_span": 2}),
        table.cells[6].model_copy(update={"role": "row_header"}),
    )
    assert infer_header_roles(cells) == ((0, 1), (0,))
    assert infer_header_roles(table.cells) == ((), ())
