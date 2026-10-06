import json

import pytest

from evaluation.manifest import CorpusManifest, Report, load_manifest, payload_digest


def report(id, split, family, company="Company"):
    return Report(
        id=id,
        company=company,
        family=family,
        year=2024,
        split=split,
        source_url="https://example.com/report.pdf",
        source_path="../data/report.pdf",
        sha256="0" * 64,
        accessed="2026-10-06",
        access_note="fixture",
    )


def manifest(reports):
    return {
        "frozen_at": "2026-10-06T00:00:00Z",
        "frozen_payload_sha256": "0" * 64,
        "reports": reports,
        "cases": [],
        "annotation_protocol": "source fixture",
        "limitations": ["test"],
    }


def test_same_company_cannot_evade_grouping_with_a_different_family_alias():
    with pytest.raises(ValueError, match="Company"):
        CorpusManifest.model_validate(
            manifest([report("a", "development", "family-a"), report("b", "heldout", "family-b")])
        )


def test_frozen_manifest_rejects_any_content_change(tmp_path):
    data = manifest([report("a", "development", "family-a").model_dump(mode="json")])
    data["frozen_payload_sha256"] = payload_digest(data)
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_manifest(path).frozen_payload_sha256 == data["frozen_payload_sha256"]
    data["reports"][0]["year"] = 2025
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_manifest(path)
