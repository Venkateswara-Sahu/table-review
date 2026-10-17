import json
import zipfile
from uuid import uuid4

from openpyxl import load_workbook

from table_review.exports.csv_package import write_csv_package
from table_review.exports.types import ExportInput
from table_review.exports.workbook import write_workbook
from table_review.tables.subtotals import evaluate_subtotals
from tests.domain.test_subtotals import definition
from tests.fixtures.subtotals import subtotal_table


def test_declared_subtotal_results_are_captured_in_both_formats_without_formulas(tmp_path):
    content = subtotal_table()
    content = content.model_copy(update={"subtotal_groups": (definition(content),)})
    results = tuple(item.model_dump(mode="json") for item in evaluate_subtotals(content))
    data = ExportInput(content=content, revision_id=uuid4(), subtotal_results=results)
    csv = write_csv_package(data, tmp_path / "sum.zip")
    with zipfile.ZipFile(csv.path) as archive:
        assert json.loads(archive.read("subtotals.json"))["results"] == list(results)
        assert "subtotals.json" in json.loads(archive.read("manifest.json"))["member_sha256"]
    wb = load_workbook(write_workbook(data, tmp_path / "sum.xlsx").path)
    rows = list(wb["Subtotals"].iter_rows(min_row=2, values_only=True))
    assert json.loads("".join(str(row[2]) for row in rows)) == results[0]
    assert all(cell.data_type != "f" for sheet in wb for row in sheet for cell in row)
