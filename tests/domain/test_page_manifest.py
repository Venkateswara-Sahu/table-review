import hashlib
import json
from datetime import UTC, datetime

import pytest

from evaluation.manifest import CorpusManifest, Report, payload_digest
from evaluation.page_manifest import validate_page_bindings, verify_execution_identity
from tests.domain.test_metrics import fixture


def packet():
    truth, _ = fixture()
    corpus = CorpusManifest(
        frozen_at=datetime.now(UTC),
        frozen_payload_sha256="0" * 64,
        reports=(
            Report(
                id="report",
                company="fixture",
                family="fixture",
                year=2025,
                split="development",
                source_url="https://example.com/report.pdf",
                source_path="report.pdf",
                sha256="0" * 64,
                accessed="2026-10-16",
                access_note="fixture",
            ),
        ),
        cases=(truth,),
        annotation_protocol="fixture",
        limitations=(),
    )
    plan = {
        "pages": [
            {"report_id": "report", "page": truth.page, "case_ids": [truth.id], "adjudicated": True}
        ]
    }
    return corpus, plan


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "unknown",
        "duplicate_page",
        "duplicate_case",
        "wrong_report",
        "wrong_page",
        "ambiguous_with_truth",
    ],
)
def test_page_bindings_reject_silent_reference_omission_or_duplicate_denominators(fault):
    corpus, plan = packet()
    entry = plan["pages"][0]
    if fault == "missing":
        entry["case_ids"] = []
    if fault == "unknown":
        entry["case_ids"] = ["typo"]
    if fault == "duplicate_page":
        plan["pages"].append(entry.copy())
    if fault == "duplicate_case":
        entry["case_ids"] *= 2
    if fault == "wrong_report":
        entry["report_id"] = "typo"
    if fault == "wrong_page":
        entry["page"] += 1
    if fault == "ambiguous_with_truth":
        entry["adjudicated"] = False
    with pytest.raises(ValueError):
        validate_page_bindings(corpus, plan)


def test_page_bindings_accept_explicit_negatives_and_unresolved_pages():
    corpus, plan = packet()
    plan["pages"].extend(
        [
            {"report_id": "report", "page": 8, "case_ids": [], "adjudicated": True},
            {"report_id": "report", "page": 9, "case_ids": [], "adjudicated": False},
        ]
    )
    validate_page_bindings(corpus, plan)


def test_execution_identity_rejects_changed_scoring_code_models_or_dependencies(tmp_path):
    scorer = tmp_path / "score.py"
    scorer.write_text("threshold=0.2", encoding="utf-8")
    frozen = {
        "files": {"score.py": hashlib.sha256(scorer.read_bytes()).hexdigest()},
        "runtime": {
            "code_revision": "parser-v1",
            "dependency_lock_sha256": "lock",
            "versions": {"docling": "2.0"},
        },
        "model_manifest_sha256": "models",
    }
    frozen["frozen_payload_sha256"] = payload_digest(frozen)
    json.loads(json.dumps(frozen))
    verify_execution_identity(frozen, tmp_path, frozen["runtime"], "models")
    scorer.write_text("threshold=0.9", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_execution_identity(frozen, tmp_path, frozen["runtime"], "models")
    scorer.write_text("threshold=0.2", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_execution_identity(frozen, tmp_path, frozen["runtime"], "changed")
    with pytest.raises(ValueError):
        verify_execution_identity(
            frozen, tmp_path, {**frozen["runtime"], "versions": {"docling": "3.0"}}, "models"
        )
