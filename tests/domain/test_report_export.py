import zipfile
from uuid import uuid4

from lxml import etree
from openpyxl import load_workbook

from table_review.documents.types import SourceReference
from table_review.exports.clipboard import (
    format_table_json,
    format_table_markdown,
    format_table_tsv,
)
from table_review.exports.types import ExportInput, ReportExportInput, ReportExportTableInput
from table_review.exports.workbook import write_report_workbook
from table_review.tables.auto_subtotals import detect_subtotals
from table_review.tables.context_types import TableContext
from table_review.tables.types import CellContent, TableContent


def _financial_table(title, rows):
    ref = SourceReference(page=5, precision="page")
    cells = []
    for r, vals in enumerate(rows):
        for c, text in enumerate(vals):
            cells.append(
                CellContent(
                    id=f"{title}-r{r}c{c}",
                    row=r,
                    col=c,
                    raw_text=text,
                    text=text,
                    role="column_header" if r == 0 else "row_header" if c == 0 else "data",
                    reference=ref,
                )
            )
    return TableContent(
        rows=len(rows),
        columns=len(rows[0]),
        cells=tuple(cells),
        header_rows=(0,),
        row_label_columns=(0,),
        title=title,
        table_context=TableContext(currency="USD", scale="millions", period="FY2025"),
        source_reference=ref,
    )


def test_report_workbook_combines_tables_with_hyperlinks_formulas_and_clean_tabs(tmp_path):
    first = _financial_table(
        "Income: [Q4] / Revenue*?",
        [["Metric", "2025"], ["Product", "$100.00"], ["Service", "50.00"], ["Total", "$150.00"]],
    )
    second = _financial_table(
        "Income: [Q4] / Revenue*?",
        [["Metric", "2025"], ["Cost", "40"], ["Tax", "10"], ["Total", "60"]],  # mismatch 50 != 60
    )
    detected_first = tuple(d.model_dump(mode="json") for d in detect_subtotals(first))
    detected_second = tuple(d.model_dump(mode="json") for d in detect_subtotals(second))

    report = ReportExportInput(
        document_id=uuid4(),
        filename="annual-report-2025.pdf",
        source_sha256="a" * 64,
        include_formulas=True,
        tables=(
            ReportExportTableInput(
                table_id=uuid4(),
                page_no=5,
                label="Table 1",
                export_input=ExportInput(
                    content=first, revision_id=uuid4(), detected_subtotals=detected_first
                ),
            ),
            ReportExportTableInput(
                table_id=uuid4(),
                page_no=6,
                label="Table 2",
                export_input=ExportInput(
                    content=second, revision_id=uuid4(), detected_subtotals=detected_second
                ),
            ),
        ),
    )

    artifact = write_report_workbook(report, tmp_path / "report.xlsx")
    assert artifact.format == "report_workbook"
    wb = load_workbook(artifact.path)
    assert wb.sheetnames[0] == "Overview"
    assert wb.sheetnames[-1] == "Source & Audit"
    sheet_one, sheet_two = wb.sheetnames[1], wb.sheetnames[2]
    assert len(sheet_one) <= 31 and len(sheet_two) <= 31
    assert sheet_one != sheet_two and sheet_two.endswith("(2)")

    overview = wb["Overview"]
    assert overview["B5"].value == sheet_one
    assert overview["B5"].hyperlink is not None
    assert f"#'{sheet_one}'!A1" in overview["B5"].hyperlink.target

    # Exact subtotal match gets live Excel formula; mismatch preserves reported value with amber fill.
    assert wb[sheet_one]["B4"].value == "=SUM(B2:B3)"
    assert wb[sheet_one]["B4"].data_type == "f"
    assert wb[sheet_one]["B2"].number_format == "$#,##0.00"
    assert wb[sheet_two]["B4"].value == 60
    assert wb[sheet_two]["B4"].fill.fgColor.rgb.endswith("FBF0DE")

    # Deterministic core.xml timestamps preserved.
    with zipfile.ZipFile(artifact.path) as archive:
        root = etree.fromstring(archive.read("docProps/core.xml"))
    terms = "http://purl.org/dc/terms/"
    assert root.find(f"{{{terms}}}created").text == "1980-01-01T00:00:00Z"


def test_clipboard_formats_tsv_markdown_and_json():
    content = _financial_table(
        "Summary",
        [["Line\tItem", "2025", "2025"], ["Sales | Core", "100", "200"]],
    )
    tsv = format_table_tsv(content)
    assert tsv == "Line Item\t2025\t2025\nSales | Core\t100\t200"

    md = format_table_markdown(content)
    assert "| Line Item | 2025 | 2025 (2) |" in md
    assert "| Sales \\| Core | 100 | 200 |" in md

    payload = format_table_json(content)
    assert payload["title"] == "Summary"
    assert payload["columns"] == ["Line Item", "2025", "2025 (2)"]
    assert payload["rows"] == [{"Line Item": "Sales | Core", "2025": "100", "2025 (2)": "200"}]
