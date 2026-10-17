import json
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from table_review.exports.csv_package import write_csv_package
from table_review.exports.types import ExportInput, export_manifest
from table_review.exports.workbook import write_workbook
from table_review.reviews.types import ReviewSnapshot
from table_review.tables.types import content_checksum
from tests.fixtures.tables import simple_table


@pytest.mark.parametrize("format", ["workbook", "csv_package"])
def test_migrated_nonempty_approval_exports_legacy_evidence_without_rewriting_snapshot(
    tmp_path, format
):
    content = simple_table(rows=1, columns=1)
    revision = uuid4()
    snapshot = ReviewSnapshot(
        id=uuid4(),
        revision_id=revision,
        content_checksum=content_checksum(content),
        label="reviewed",
        checked_cell_ids=("r0c0",),
        issue_decisions={},
        check_versions={"fixture": "ran"},
        checks_not_run=False,
        unresolved_count=0,
        actor_id=uuid4(),
        approved_at=datetime.now(UTC),
    )
    original = snapshot.model_dump_json()
    data = ExportInput(content=content, revision_id=revision, review_snapshot=snapshot)
    manifest = export_manifest(data, format)
    record = manifest["review"]["cell_reviews"]["r0c0"]
    assert record["verdict"] == "confirmed_value" and record["origin"] == "legacy"
    assert record["actor_id"] is None and record["decided_at"] is None
    target = tmp_path / ("out.xlsx" if format == "workbook" else "out.zip")
    (write_workbook if format == "workbook" else write_csv_package)(data, target)
    if format == "workbook":
        source = load_workbook(target)["Source"]
        assert "legacy" in source.cell(2, 11).value
    else:
        with zipfile.ZipFile(target) as archive:
            assert json.loads(archive.read("cells.json"))[0]["cell_review"] == record
    assert snapshot.model_dump_json() == original
