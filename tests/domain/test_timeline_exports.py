import io
import json
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

from openpyxl import load_workbook

from table_review.exports.csv_package import write_csv_package
from table_review.exports.types import ExportInput
from table_review.exports.workbook import write_workbook
from tests.fixtures.tables import simple_table


def test_spreadsheet_formats_retain_the_complete_captured_timeline(tmp_path):
    revision = uuid4()
    event = {
        "id": str(uuid4()),
        "table_id": str(uuid4()),
        "revision_id": str(revision),
        "sequence": 1,
        "kind": "edit",
        "actor_id": str(uuid4()),
        "recorded_at": datetime.now(UTC).isoformat(),
        "cell_ids": ["r1c1"],
        "details": {"reason": "Dependent review reopened", "before": "x" * 12000, "after": "42"},
    }
    data = ExportInput(
        content=simple_table(),
        revision_id=revision,
        timeline_events=(event,),
        timeline_through_sequence=1,
    )
    csv = write_csv_package(data, tmp_path / "timeline.zip")
    with zipfile.ZipFile(csv.path) as archive:
        timeline = json.loads(archive.read("timeline.json"))
        assert timeline["events"] == [event] and timeline["through_sequence"] == 1
        assert "timeline.json" in json.loads(archive.read("manifest.json"))["member_sha256"]
    book = load_workbook(
        io.BytesIO(write_workbook(data, tmp_path / "timeline.xlsx").path.read_bytes())
    )
    assert "Timeline" in book.sheetnames
    rows = list(book["Timeline"].iter_rows(min_row=2, values_only=True))
    parts = sorted((int(row[1]), str(row[2])) for row in rows if row[0] == event["id"])
    assert len(parts) > 10 and [part for part, _ in parts] == list(range(1, len(parts) + 1))
    restored = json.loads("".join(chunk for _, chunk in parts))
    assert restored == event
