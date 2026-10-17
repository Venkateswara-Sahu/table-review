from table_review.documents.types import PageBox, SourceReference
from tests.fixtures.tables import simple_table


def source_table(left=10, page=2, text=None):
    content = simple_table(1, 1)
    cell = content.cells[0]
    if text is not None:
        cell = cell.model_copy(update={"raw_text": text, "text": text})
    return content.model_copy(
        update={
            "cells": (cell,),
            "source_reference": SourceReference(
                page=page,
                precision="table",
                box=PageBox(
                    page=page,
                    left=left,
                    top=10,
                    right=left + 100,
                    bottom=110,
                    page_width=612,
                    page_height=792,
                ),
            ),
        }
    )


def compare(before, after):
    from table_review.extraction.comparison import compare_tables

    return compare_tables(before, after)


def test_comparison_ignores_generated_ids_but_reports_literal_and_span_changes():
    before = source_table(text="(1,234)")
    renamed = before.model_copy(
        update={"cells": (before.cells[0].model_copy(update={"id": "different-generated-id"}),)}
    )
    assert compare(before, renamed)["changed_cells"] == []
    diff = compare(before, source_table(text="1,234"))
    assert diff["changed_cells"][0]["before"]["text"] == "(1,234)"
    assert diff["changed_cells"][0]["after"]["text"] == "1,234"
    assert diff["changed_cells"][0]["fields"] == ["raw_text", "text"]


def test_matching_uses_source_evidence_not_extractor_position_or_cell_ids():
    from table_review.extraction.comparison import match_tables

    before = {"a": source_table(10), "b": source_table(200)}
    after = {"new-b": source_table(200, text="changed"), "new-a": source_table(10)}
    result = match_tables(before, after)
    assert {(m["before_id"], m["after_id"]) for m in result["matches"]} == {
        ("a", "new-a"),
        ("b", "new-b"),
    }
    assert result["ambiguous"] == []


def test_overlapping_candidates_are_ambiguous_not_silently_assigned():
    from table_review.extraction.comparison import match_tables

    result = match_tables({"a": source_table()}, {"x": source_table(), "y": source_table(11)})
    assert result["matches"] == []
    assert result["ambiguous"] == [{"before_id": "a", "candidate_ids": ["x", "y"]}]


def test_coarse_reference_cannot_establish_identity_even_for_identical_values():
    from table_review.extraction.comparison import match_tables

    coarse = source_table().model_copy(
        update={"source_reference": SourceReference(page=2, precision="page")}
    )
    result = match_tables({"a": coarse}, {"b": coarse})
    assert result["matches"] == [] and result["unmatched_before"] == ["a"]


def test_table_context_and_source_geometry_changes_are_included():
    before = source_table()
    after = before.model_copy(update={"unit_notes": ("EUR thousands",), "header_rows": (0,)})
    assert compare(before, after)["table_changes"] == {
        "header_rows": {"before": [], "after": [0]},
        "unit_notes": {"before": ["USD millions"], "after": ["EUR thousands"]},
    }
    moved = source_table(11)
    assert "source_reference" in compare(before, moved)["table_changes"]
