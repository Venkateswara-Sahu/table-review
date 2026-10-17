import io
import json
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

from openpyxl import load_workbook

from table_review.exports.csv_package import write_csv_package
from table_review.exports.types import ExportInput
from table_review.exports.workbook import write_workbook
from table_review.tables.context import ContextIndex
from table_review.tables.grid import apply_edit, parse_edit
from tests.domain.test_source_context import contextual_table


def confirmed_table():
    table = contextual_table()
    candidate = next(
        item for item in ContextIndex(table).cell("r2c1").proposals if item.kind == "period"
    )
    table = apply_edit(
        table,
        parse_edit({"kind": "confirm_context", "cell_ids": ["r2c1"], "candidate_id": candidate.id}),
    ).content
    return table.model_copy(
        update={
            "context_bindings": tuple(
                binding.model_copy(
                    update={
                        "confirmed_by": uuid4(),
                        "confirmed_at": datetime.now(UTC),
                    }
                )
                for binding in table.context_bindings
            )
        }
    )


def test_exports_preserve_context_sources_and_confirmation_without_rescaling(tmp_path):
    table = confirmed_table()
    data = ExportInput(content=table, revision_id=uuid4())
    csv = write_csv_package(data, tmp_path / "context.zip")
    with zipfile.ZipFile(csv.path) as archive:
        context = json.loads(archive.read("context.json"))
        row = next(item for item in context["cells"] if item["cell_id"] == "r2c1")
        assert row["confirmed"][0]["value"] == "2024" and row["confirmed"][0]["confirmed_by"]
        assert "cell:year-2024" in context["sources"]
        assert context["sources"]["cell:year-2024"]["references"][0]["precision"] == "page"
        cells = json.loads(archive.read("cells.json"))
        numeric = next(item for item in cells if item["id"] == "r2c1")
        assert numeric["text"] == "(12.50)" and float(numeric["numeric_value"]) == -12.5
    workbook = write_workbook(data, tmp_path / "context.xlsx")
    book = load_workbook(io.BytesIO(workbook.path.read_bytes()))
    assert "Context" in book.sheetnames
    assert book["Table"]["B3"].value == -12.5
    text = "".join(str(cell.value) for row in book["Context"] for cell in row)
    assert "confirmed_by" in text and "year-2024" in text
