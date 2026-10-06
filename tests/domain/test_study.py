from datetime import UTC, datetime, timedelta

import pytest

from evaluation.study import StudyTrial, assign_conditions, summarize_study


def trial(**changes):
    start = datetime(2026, 10, 6, tzinfo=UTC)
    values = dict(  # noqa: C408 - readable test fixture keywords
        participant_id="rehearsal-1",
        document_case_id="case-1",
        matched_block="block-1",
        condition="review_with_checks",
        start=start,
        end=start + timedelta(seconds=60),
        processing_time=20,
        final_result=None,
        outcome="failed",
        experience="automated_fixture",
        evidence_kind="developer_rehearsal",
        extraction_sha256="a" * 64,
    )
    return StudyTrial(**(values | changes))


def test_failed_tasks_count_with_full_time_including_processing():
    summary = summarize_study((trial(),))
    assert summary.groups[0].task_count == summary.groups[0].failed_count == 1
    assert summary.groups[0].median_full_seconds == 60
    assert summary.groups[0].processing_seconds == 20
    assert summary.claim_status == "developer_rehearsal"
    assert summary.external_participant_count == 0
    assert summary.median_savings is None


def test_same_person_cannot_reuse_same_table_under_other_conditions():
    with pytest.raises(ValueError, match="reuse"):
        summarize_study((trial(), trial(condition="review_without_checks")))


def test_processing_cannot_exceed_full_elapsed_time():
    with pytest.raises(ValueError):
        trial(processing_time=61)
    with pytest.raises(ValueError):
        trial(start=datetime(2026, 10, 6))  # noqa: DTZ001 - intentionally invalid


def test_review_conditions_must_use_the_same_frozen_extraction():
    with pytest.raises(ValueError, match="frozen extraction"):
        summarize_study(
            (
                trial(),
                trial(
                    participant_id="rehearsal-2",
                    condition="review_without_checks",
                    extraction_sha256="b" * 64,
                ),
            )
        )


def test_assignment_rotates_conditions_across_matched_distinct_documents():
    assignments = assign_conditions(("p1", "p2", "p3"), ("case1", "case2", "case3"), "block")
    assert len(assignments) == 9
    assert {a.condition for a in assignments if a.document_case_id == "case1"} == {
        "spreadsheet_baseline",
        "review_without_checks",
        "review_with_checks",
    }
    for participant in ("p1", "p2", "p3"):
        rows = [a for a in assignments if a.participant_id == participant]
        assert len({a.document_case_id for a in rows}) == 3
        assert len({a.condition for a in rows}) == 3


def test_developer_results_never_pool_into_external_comparisons():
    external = trial(participant_id="external-1", evidence_kind="external", experience="occasional")
    summary = summarize_study((trial(), external))
    assert summary.external_participant_count == 1
    assert len(summary.groups) == 2
    assert summary.median_savings is None
    assert summary.claim_status == "exploratory_external_incomplete"


def test_matched_external_summary_includes_failed_condition_and_audited_quality():
    from evaluation.study import FinalAudit

    audit = FinalAudit(
        values_correct=1,
        values_total=2,
        headers_correct=1,
        headers_total=1,
        units_correct=0,
        units_total=1,
        grader="synthetic-test",
        artifact_sha256="a" * 64,
    )
    rows = []
    for participant in ("external-1", "external-2"):
        for index, condition in enumerate(
            ("spreadsheet_baseline", "review_without_checks", "review_with_checks")
        ):
            rows.append(
                trial(
                    participant_id=participant,
                    document_case_id=f"case-{index}",
                    condition=condition,
                    evidence_kind="external",
                    experience="occasional",
                    end=datetime(2026, 10, 6, tzinfo=UTC) + timedelta(seconds=(100, 90, 75)[index]),
                    final_audit=audit,
                )
            )
    summary = summarize_study(tuple(rows))
    assert summary.matched_external_blocks == 2
    assert summary.median_savings == 0.25
    assert summary.target_status == "met_in_this_sample"
    assert summary.exploratory_bootstrap_interval == (0.25, 0.25)
    assert all(group.failed_count == 2 for group in summary.groups)
    assert all(group.audited_counts["units_correct"] == 0 for group in summary.groups)
