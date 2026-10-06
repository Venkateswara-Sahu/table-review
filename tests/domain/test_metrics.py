import pytest

from evaluation.manifest import CaseTruth
from evaluation.metrics import CaseResult, score_case, summarize_cases
from table_review.documents.types import PageBox, SourceReference
from table_review.tables.types import CellContent, TableContent


def fixture():
    box = PageBox(page=2, left=10, top=10, right=40, bottom=30, page_width=100, page_height=100)
    cells = (
        CellContent(
            id="h",
            row=0,
            col=1,
            text="2025",
            raw_text="2025",
            role="column_header",
            reference=SourceReference(page=2, precision="cell", box=box),
        ),
        CellContent(
            id="l",
            row=1,
            col=0,
            text="Loss",
            raw_text="Loss",
            role="row_header",
            reference=SourceReference(page=2, precision="cell", box=box),
        ),
        CellContent(
            id="v",
            row=1,
            col=1,
            text="(1,250)",
            raw_text="(1,250)",
            reference=SourceReference(page=2, precision="cell", box=box),
        ),
    )
    content = TableContent(
        rows=2,
        columns=2,
        cells=cells,
        header_rows=(0,),
        row_label_columns=(0,),
        unit_notes=("USD millions",),
    )
    truth = CaseTruth(
        id="fixture",
        report_id="report",
        split="development",
        page=2,
        region=box,
        content=content,
        annotation_method="source-verified test fixture",
        annotation_status="complete",
    )
    return truth, content


def test_exact_literals_and_associations_score_without_rounding():
    truth, content = fixture()
    metrics = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=content))
    assert metrics.literal_correct == metrics.literal_total == 3
    assert metrics.association_correct == metrics.association_total == 1
    assert metrics.unit_correct and metrics.structure_correct and metrics.exact_table


@pytest.mark.parametrize("value", ["1,250", "(1.250)", "(1250)", "(1,250)(a)", "(1,250)−"])
def test_sign_scale_punctuation_and_footnotes_are_not_normalized_away(value):
    truth, content = fixture()
    changed = content.model_copy(
        update={"cells": (*content.cells[:2], content.cells[2].model_copy(update={"text": value}))}
    )
    metrics = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=changed))
    assert metrics.literal_correct == 2
    assert not metrics.exact_table


def test_correct_value_under_wrong_header_is_an_association_failure():
    truth, content = fixture()
    changed = content.model_copy(
        update={"cells": (content.cells[0].model_copy(update={"text": "2024"}), *content.cells[1:])}
    )
    metrics = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=changed))
    assert metrics.association_correct == 0 and metrics.association_total == 1


@pytest.mark.parametrize("kind", ["coarse", "wrong_page", "wrong_box"])
def test_geometry_requires_precise_reference_on_the_correct_source(kind):
    truth, content = fixture()
    original = content.cells[2].reference
    ref = (
        SourceReference(page=2, precision="page")
        if kind == "coarse"
        else original.model_copy(update={"page": 3})
        if kind == "wrong_page"
        else original.model_copy(
            update={"box": original.box.model_copy(update={"left": 60, "right": 90})}
        )
    )
    changed = content.model_copy(
        update={
            "cells": (*content.cells[:2], content.cells[2].model_copy(update={"reference": ref}))
        }
    )
    metrics = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=changed))
    assert metrics.geometry_correct == 2 and metrics.geometry_total == 3


def test_unit_disagreement_and_role_disagreement_are_separate_counts():
    truth, content = fixture()
    changed = content.model_copy(
        update={
            "unit_notes": ("USD thousands",),
            "cells": (content.cells[0].model_copy(update={"role": "data"}), *content.cells[1:]),
        }
    )
    metrics = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=changed))
    assert not metrics.unit_correct
    assert metrics.role_correct == 2 and metrics.role_total == 3


def test_failed_and_missing_cases_remain_in_all_denominators():
    truth, content = fixture()
    success = CaseResult(case_id="fixture", truth=truth, content=content)
    failed_truth = truth.model_copy(update={"id": "failed"})
    failed = CaseResult(case_id="failed", truth=failed_truth, failure_code="timeout")
    missing_truth = truth.model_copy(update={"id": "missing"})
    missing = CaseResult(case_id="missing", truth=missing_truth, table_detected=False)
    summary = summarize_cases((success, failed, missing))
    assert summary.case_count == 3 and summary.failure_count == 1 and summary.missing_count == 1
    assert summary.literal_total == 9 and summary.literal_correct == 3
    assert summary.literal_accuracy == pytest.approx(1 / 3)
    assert summary.exact_table_count == 1


def test_empty_denominators_are_unestablished_and_duplicate_cases_rejected():
    summary = summarize_cases(())
    assert summary.case_count == 0 and summary.literal_accuracy is None
    truth, content = fixture()
    result = CaseResult(case_id=truth.id, truth=truth, content=content)
    with pytest.raises(ValueError):
        summarize_cases((result, result))


def test_header_corner_is_not_a_value_association_and_legacy_values_are():
    truth, content = fixture()
    corner = content.cells[0].model_copy(
        update={"id": "corner", "col": 0, "role": "data", "text": "Year"}
    )
    value = content.cells[2].model_copy(update={"role": "value"})
    content = content.model_copy(update={"cells": (corner, *content.cells[:2], value)})
    truth = truth.model_copy(update={"content": content})
    scored = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=content))
    assert scored.association_total == scored.association_correct == 1


def test_a_result_cannot_silently_reference_another_case():
    truth, content = fixture()
    with pytest.raises(ValueError, match="identity"):
        CaseResult(case_id="different", truth=truth, content=content)


def test_comparisons_cannot_mix_modes_or_matching_settings():
    truth, content = fixture()
    other = truth.model_copy(update={"id": "other"})
    with pytest.raises(ValueError, match="configuration"):
        summarize_cases(
            (
                CaseResult(case_id=truth.id, truth=truth, content=content),
                CaseResult(case_id="other", truth=other, content=content, mode="crop"),
            )
        )


def test_truth_without_precise_geometry_is_not_a_geometry_denominator():
    truth, content = fixture()
    cells = (
        *content.cells[:2],
        content.cells[2].model_copy(
            update={"reference": SourceReference(page=2, precision="table", box=truth.region)}
        ),
    )
    truth = truth.model_copy(update={"content": content.model_copy(update={"cells": cells})})
    metrics = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=content))
    assert metrics.geometry_total == metrics.geometry_correct == 2


def test_unordered_literal_occurrences_do_not_claim_correct_associations():
    truth, content = fixture()
    changed = content.model_copy(update={"header_rows": (), "row_label_columns": ()})
    metrics = score_case(truth, CaseResult(case_id=truth.id, truth=truth, content=changed))
    assert metrics.literal_occurrence_correct == metrics.literal_occurrence_total == 3
    assert metrics.association_correct == 0
