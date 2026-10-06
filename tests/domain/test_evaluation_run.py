import json
from datetime import UTC, datetime
from pathlib import Path

from evaluation.manifest import CorpusManifest, Report
from evaluation.metrics import CaseResult
from evaluation.run import evaluate, evaluate_group, select_table
from table_review.extraction.types import ExtractedTable, ExtractionResult
from table_review.jobs.runner import ProcessingFailure
from tests.domain.test_metrics import fixture


def test_table_selection_requires_the_correct_original_page_and_region():
    truth, content = fixture()
    wrong_page = ExtractedTable(
        id="wrong",
        page_no=1,
        region=truth.region.model_copy(update={"page": 1}),
        content=content,
        source_tokens=(),
    )
    correct = ExtractedTable(
        id="right", page_no=truth.page, region=truth.region, content=content, source_tokens=()
    )
    result = ExtractionResult(tables=(wrong_page, correct), page_info=(), warnings=(), manifest={})
    assert select_table(truth, result).id == "right"
    assert select_table(truth, result.model_copy(update={"tables": (wrong_page,)})) is None


def test_unavailable_source_counts_as_failure_without_loading_a_model(tmp_path):
    truth, _ = fixture()
    report = Report(
        id="report",
        company="Fixture",
        family="fixture",
        year=2024,
        split="development",
        source_url="https://example.com/report.pdf",
        source_path="missing.pdf",
        sha256="0" * 64,
        accessed="2026-10-06",
        access_note="fixture",
    )
    corpus = CorpusManifest(
        frozen_at=datetime.now(UTC),
        frozen_payload_sha256="0" * 64,
        reports=(report,),
        cases=(truth,),
        annotation_protocol="fixture",
        limitations=(),
        manifest_path=tmp_path / "manifest.json",
    )

    class ShouldNotRun:
        def extract(self, request):
            raise AssertionError("Missing source must not execute a model")

    records = evaluate_group(
        corpus, tmp_path / "output", "development", "full_page", True, extractor=ShouldNotRun()
    )
    assert len(records) == 1
    assert records[0].failure_code == "source_unavailable"
    assert (tmp_path / "output" / "results.json").is_file()


def test_interrupted_comparison_preserves_completed_cases_and_counts_the_rest(
    monkeypatch, tmp_path
):
    truth, content = fixture()
    second = truth.model_copy(update={"id": "unfinished"})
    report = Report(
        id="report",
        company="Fixture",
        family="fixture",
        year=2024,
        split="development",
        source_url="https://example.com/report.pdf",
        source_path="missing.pdf",
        sha256="0" * 64,
        accessed="2026-10-06",
        access_note="fixture",
    )
    saved = tmp_path / "manifest.json"
    saved.write_text("{}", encoding="utf-8")
    corpus = CorpusManifest(
        frozen_at=datetime.now(UTC),
        frozen_payload_sha256="0" * 64,
        reports=(report,),
        cases=(truth, second),
        annotation_protocol="fixture",
        limitations=(),
        manifest_path=saved,
    )

    def interrupted(command, limits, **kwargs):
        destination = Path(command[command.index("--output") + 1])
        destination.mkdir()
        mode = command[command.index("--mode") + 1]
        matching = command[command.index("--matching") + 1] == "on"
        completed = CaseResult(
            case_id=truth.id, truth=truth, content=content, mode=mode, cell_matching=matching
        )
        (destination / "results.json").write_text(
            json.dumps([completed.model_dump(mode="json")]), encoding="utf-8"
        )
        raise ProcessingFailure("timeout")

    monkeypatch.setattr("evaluation.run.run_child", interrupted)
    summary = evaluate(corpus, tmp_path / "results", "development")
    assert summary.case_count == 2 and summary.failure_count == 1
    assert summary.literal_total == 6 and summary.literal_correct == 3
    comparison = json.loads((tmp_path / "results" / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["groups"]["full_page-matching-on"]["process_metrics"]["elapsed_seconds"] >= 0


def test_unit_change_does_not_credit_refingerprinted_unrelated_warnings(monkeypatch, tmp_path):
    from uuid import uuid4

    from evaluation.run import injected_checks
    from table_review.checks.types import Issue

    truth, _ = fixture()
    corpus = CorpusManifest(
        frozen_at=datetime.now(UTC),
        frozen_payload_sha256="0" * 64,
        reports=(
            Report(
                id="report",
                company="Fixture",
                family="fixture",
                year=2024,
                split="development",
                source_url="https://example.com/report.pdf",
                source_path="fixture.pdf",
                sha256="0" * 64,
                accessed="2026-10-06",
                access_note="fixture",
            ),
        ),
        cases=(truth,),
        annotation_protocol="fixture",
        limitations=(),
        manifest_path=tmp_path / "manifest.json",
    )
    monkeypatch.setattr(
        "evaluation.run._source_status", lambda *args: (tmp_path / "fixture.pdf", None)
    )
    monkeypatch.setattr("evaluation.run.source_tokens", lambda *args: ())

    def existing_warning(content, context):
        return (
            Issue(
                id=uuid4(),
                kind="coverage_discrepancy",
                affected_cell_ids=("v",),
                evidence={"source": "unchanged"},
                reason="An existing coverage warning",
                fingerprint=str(content.unit_notes),
            ),
        )

    monkeypatch.setattr("evaluation.run.evaluate_checks", existing_warning)
    unit = next(row for row in injected_checks(corpus, "development") if row["kind"] == "unit")
    assert unit["detected_new_target_issue"] is False
