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


def test_declared_subtotal_results_write_live_formulas_and_readable_subtotals_sheet(tmp_path):
    content = subtotal_table()
    content = content.model_copy(update={"subtotal_groups": (definition(content),)})
    results = tuple(item.model_dump(mode="json") for item in evaluate_subtotals(content))
    data = ExportInput(content=content, revision_id=uuid4(), subtotal_results=results)

    csv = write_csv_package(data, tmp_path / "sum.zip")
    with zipfile.ZipFile(csv.path) as archive:
        assert json.loads(archive.read("subtotals.json"))["results"] == list(results)
        assert "subtotals.json" in json.loads(archive.read("manifest.json"))["member_sha256"]

    wb = load_workbook(write_workbook(data, tmp_path / "sum.xlsx").path)
    assert wb["Table"]["B4"].value == "=SUM(B2:B3)"
    assert wb["Table"]["B4"].data_type == "f"
    rows = list(wb["Subtotals"].iter_rows(min_row=1, values_only=True))
    assert rows[0] == (
        "Name",
        "Origin",
        "Total Cell",
        "Formula",
        "Reported Total",
        "Computed Sum",
        "Difference",
        "Status",
        "Notes",
    )
    assert rows[1][0] == "Declared operating total"
    assert rows[1][1] == "Declared"
    assert rows[1][2] == "B4"
    assert rows[1][3] == "=SUM(B2:B3)"
    assert rows[1][4] == 8.05
    assert rows[1][5] == "=SUM('Table'!B2:B3)"
    assert rows[1][6] == "=E2-F2"
    assert rows[1][7] == "match"


def test_static_export_option_writes_static_values_without_formulas(tmp_path):
    content = subtotal_table()
    content = content.model_copy(update={"subtotal_groups": (definition(content),)})
    results = tuple(item.model_dump(mode="json") for item in evaluate_subtotals(content))
    data = ExportInput(
        content=content,
        revision_id=uuid4(),
        subtotal_results=results,
        include_formulas=False,
    )
    wb = load_workbook(write_workbook(data, tmp_path / "static.xlsx").path)
    assert wb["Table"]["B4"].value == 8.05
    assert wb["Table"]["B4"].data_type == "n"
    assert all(cell.data_type != "f" for sheet in wb for row in sheet for cell in row)
