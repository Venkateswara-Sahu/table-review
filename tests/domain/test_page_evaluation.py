import pytest

from evaluation.pages import score_page
from table_review.extraction.types import ExtractedTable, ExtractionResult
from tests.domain.test_metrics import fixture


def prediction(*tables):
    return ExtractionResult(tables=tables, page_info=(), warnings=(), manifest={})


def table(truth, content, identity="predicted", **updates):
    return ExtractedTable(
        id=identity,
        page_no=truth.page,
        region=truth.region,
        content=content,
        source_tokens=(),
    ).model_copy(update=updates)


def test_duplicate_predictions_are_false_positives_not_extra_true_positives():
    truth, content = fixture()
    scored = score_page(
        (truth,), prediction(table(truth, content), table(truth, content, "dup")), page=truth.page
    )
    assert scored["true_positive_tables"] == 1
    assert scored["false_positive_tables"] == 1
    assert scored["false_negative_tables"] == 0
    assert scored["cell_summary"]["exact_table_count"] == 1
    assert scored["exact_page"] is False


def test_negative_pages_count_every_prediction_including_missing_geometry():
    truth, content = fixture()
    scored = score_page((), prediction(table(truth, content, region=None)), page=truth.page)
    assert scored["false_positive_tables"] == 1
    assert not scored["exact_page"]
    assert score_page((), prediction(), page=truth.page)["exact_page"]


def test_failure_keeps_all_reference_cells_and_tables_in_denominators():
    truth, _ = fixture()
    scored = score_page((truth,), None, page=truth.page, failure_code="timeout")
    assert scored["false_negative_tables"] == 1
    assert scored["failed_pages"] == 1
    assert scored["cell_summary"]["literal_total"] == len(truth.content.cells)
    assert scored["cell_summary"]["literal_correct"] == 0
    assert not scored["exact_page"]


def test_ambiguous_pages_are_explicitly_unscored_not_successful_negatives():
    truth, content = fixture()
    scored = score_page((), prediction(table(truth, content)), page=truth.page, adjudicated=False)
    assert scored["scored"] is False
    assert scored["predicted_tables"] == 1
    assert scored["false_positive_tables"] is None
    assert scored["exact_page"] is None


def test_wrong_page_predictions_cannot_match_original_page_truth():
    truth, content = fixture()
    scored = score_page(
        (truth,), prediction(table(truth, content, page_no=truth.page + 1)), page=truth.page
    )
    assert scored["false_negative_tables"] == scored["false_positive_tables"] == 1


def test_matching_finds_maximum_pairs_instead_of_greedy_duplicate_assignment():
    truth, content = fixture()
    # Wide prediction matches both references, narrow prediction only the first.
    region = truth.region
    first = truth.model_copy(update={"region": region.model_copy(update={"left": 0, "right": 40})})
    second = truth.model_copy(
        update={"id": "second", "region": region.model_copy(update={"left": 45, "right": 90})}
    )
    wide = table(truth, content, "wide", region=region.model_copy(update={"left": 0, "right": 90}))
    narrow = table(truth, content, "narrow", region=first.region)
    scored = score_page((first, second), prediction(wide, narrow), page=truth.page)
    assert scored["true_positive_tables"] == 2
    assert scored["false_negative_tables"] == scored["false_positive_tables"] == 0


def test_inconsistent_failure_and_prediction_is_rejected():
    truth, _ = fixture()
    with pytest.raises(ValueError):
        score_page((truth,), prediction(), page=truth.page, failure_code="timeout")


def test_retained_parser_failure_cannot_count_as_a_successful_negative_page():
    failed = prediction().model_copy(update={"manifest": {"conversion_outcome": "failure"}})
    scored = score_page((), failed, page=1)
    assert scored["failed_pages"] == 1
    assert scored["parser_conversion_outcome"] == "failure"
    assert scored["failure_code"] == "parser_conversion_failure"
    assert not scored["exact_page"]


def test_partial_parser_outcome_preserves_usable_tables_and_visible_diagnostics():
    truth, content = fixture()
    partial = prediction(table(truth, content)).model_copy(
        update={"manifest": {"conversion_outcome": "partial_success"}}
    )
    scored = score_page((truth,), partial, page=truth.page)
    assert scored["partial_pages"] == 1
    assert scored["failed_pages"] == 0
    assert scored["cell_summary"]["exact_table_count"] == 1
    assert scored["parser_conversion_outcome"] == "partial_success"
    assert not scored["exact_page"]
