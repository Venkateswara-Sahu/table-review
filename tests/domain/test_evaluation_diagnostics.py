import pytest

from evaluation.diagnostics import diagnose_case
from evaluation.metrics import CaseResult, score_case
from table_review.tables.context_types import ContextSource
from table_review.tables.types import CellContent, TableContent
from tests.domain.test_metrics import fixture


def merged_section():
    truth, original = fixture()
    reference = original.cells[0].reference
    cells = tuple(
        CellContent(
            id=str(col),
            row=0,
            col=col,
            text="ASSETS" if col == 0 else "",
            raw_text="ASSETS" if col == 0 else "",
            reference=reference,
        )
        for col in range(3)
    )
    content = TableContent(rows=1, columns=3, cells=cells)
    truth = truth.model_copy(update={"content": content})
    merged = content.model_copy(update={"cells": (cells[0].model_copy(update={"col_span": 3}),)})
    return truth, merged


def test_merged_blank_slots_are_diagnosed_separately_without_changing_strict_scores():
    truth, merged = merged_section()
    strict = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=merged))
    assert strict.literal_correct == 0 and not strict.structure_correct
    result = diagnose_case(truth, merged)
    assert result["expanded_slot_literal_correct"] == result["expanded_slot_literal_total"] == 3
    assert result["expanded_slot_literals_exact"]
    assert result["nonblank_anchor_literal_correct"] == 1
    assert not strict.exact_table


@pytest.mark.parametrize("value", ["LIABILITIES", "ASSETS1", "(ASSETS)"])
def test_expansion_does_not_normalize_away_a_changed_literal(value):
    truth, merged = merged_section()
    changed = merged.model_copy(
        update={"cells": (merged.cells[0].model_copy(update={"text": value}),)}
    )
    result = diagnose_case(truth, changed)
    assert result["expanded_slot_literal_correct"] == 2
    assert result["nonblank_anchor_literal_correct"] == 0
    assert not result["expanded_slot_literals_exact"]


def test_a_merged_cell_cannot_hide_an_expected_numeric_value():
    truth, merged = merged_section()
    cells = list(truth.content.cells)
    cells[1] = cells[1].model_copy(update={"text": "123.45", "raw_text": "123.45"})
    truth = truth.model_copy(
        update={"content": truth.content.model_copy(update={"cells": tuple(cells)})}
    )
    result = diagnose_case(truth, merged)
    assert result["expanded_slot_literal_correct"] == 2
    assert result["nonblank_anchor_literal_correct"] == 1
    assert result["nonblank_anchor_literal_total"] == 2
    assert not result["expanded_slot_literals_exact"]


def test_failed_or_missing_table_keeps_all_reference_positions_in_denominators():
    truth, _ = merged_section()
    result = diagnose_case(truth, None)
    assert result["expanded_slot_literal_correct"] == 0
    assert result["expanded_slot_literal_total"] == 3
    assert result["nonblank_anchor_literal_total"] == 1
    assert not result["expanded_slot_literals_exact"]


def test_extra_output_dimensions_prevent_an_exact_projection():
    truth, merged = merged_section()
    extra = merged.model_copy(update={"rows": 2})
    result = diagnose_case(truth, extra)
    assert result["expanded_slot_literal_correct"] == 3
    assert not result["expanded_slot_literals_exact"]


def test_caption_evidence_is_separate_from_legacy_notes_and_human_confirmation():
    truth, content = fixture()
    source = ContextSource(
        id="caption",
        text="USD millions",
        raw_text="USD millions",
        references=(content.cells[0].reference,),
        origin="extracted",
    )
    extracted = content.model_copy(update={"unit_notes": (), "context_sources": (source,)})
    result = diagnose_case(truth, extracted)
    assert result["source_caption_literal_supported"] == result["source_caption_literal_total"] == 1
    assert extracted.context_bindings == ()
    assert not score_case(
        truth, CaseResult(case_id=truth.id, truth=truth, content=extracted)
    ).unit_correct
    for proposal in (
        source.model_copy(update={"origin": "current_draft"}),
        source.model_copy(update={"truncated": True}),
        source.model_copy(update={"text": "EUR millions"}),
    ):
        candidate = extracted.model_copy(update={"context_sources": (proposal,)})
        assert diagnose_case(truth, candidate)["source_caption_literal_supported"] == 0
