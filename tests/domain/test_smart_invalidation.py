from table_review.tables.grid import apply_edit, parse_edit
from tests.domain.test_context_exports import confirmed_table


def test_metadata_edits_preserve_all_cell_context_and_report_no_affected_cells():
    table = confirmed_table()

    titled = apply_edit(table, parse_edit({"kind": "set_title", "title": "  Income Statement  "}))
    assert not titled.structural_change
    assert titled.affected_cell_ids == ()
    assert titled.content.title == "Income Statement"
    assert titled.content.context_bindings == table.context_bindings

    noted = apply_edit(
        titled.content, parse_edit({"kind": "set_notes", "unit_notes": ["USD in millions"]})
    )
    assert not noted.structural_change
    assert noted.affected_cell_ids == ()
    assert noted.content.context_bindings == table.context_bindings

    ctx_edited = apply_edit(
        noted.content,
        parse_edit(
            {
                "kind": "set_table_context",
                "currency": "USD",
                "scale": "millions",
                "period": "FY2024",
            }
        ),
    )
    assert not ctx_edited.structural_change
    assert ctx_edited.affected_cell_ids == ()
    assert ctx_edited.content.table_context is not None
    assert ctx_edited.content.table_context.currency == "USD"
    assert ctx_edited.content.context_bindings == table.context_bindings


def test_atomic_set_cell_and_surgical_row_edits_preserve_untouched_cells():
    table = confirmed_table()
    # Delete unrelated bottom row (row 4: Earnings per share) -> r2c1 keeps its context!
    deleted_bottom = apply_edit(table, parse_edit({"kind": "delete_row", "index": 4}))
    assert "r2c1" not in deleted_bottom.affected_cell_ids
    assert deleted_bottom.content.context_bindings == table.context_bindings

    # Insert row at bottom -> only new row cells are affected.
    inserted_bottom = apply_edit(
        deleted_bottom.content, parse_edit({"kind": "insert_row", "index": 4})
    )
    assert "r2c1" not in inserted_bottom.affected_cell_ids
    assert len(inserted_bottom.affected_cell_ids) == table.columns
    assert inserted_bottom.content.context_bindings == table.context_bindings

    # Atomic set_cell updates both text and interpretation, affecting only r3c1.
    updated = apply_edit(
        inserted_bottom.content,
        parse_edit(
            {
                "kind": "set_cell",
                "cell_id": "r3c1",
                "text": "—",
                "interpretation": "dash_zero",
            }
        ),
    )
    assert updated.affected_cell_ids == ("r3c1",)
    assert updated.content.context_bindings == table.context_bindings
    cell = next(c for c in updated.content.cells if c.id == "r3c1")
    assert cell.text == "—" and cell.interpretation == "dash_zero"


def test_deleting_header_row_invalidates_dependent_cells_and_bindings():
    table = confirmed_table()
    # Deleting header row 0 removes "year-2024" and invalidates r2c1's header ancestry.
    deleted_header = apply_edit(table, parse_edit({"kind": "delete_row", "index": 0}))
    assert "r2c1" in deleted_header.affected_cell_ids
    assert deleted_header.content.context_bindings == ()


def test_merge_and_split_invalidate_only_merged_cells_and_dependents():
    table = confirmed_table()
    merged = apply_edit(
        table, parse_edit({"kind": "merge", "row": 3, "col": 1, "rows": 1, "columns": 2})
    )
    assert "r2c1" not in merged.affected_cell_ids
    assert merged.content.context_bindings == table.context_bindings
