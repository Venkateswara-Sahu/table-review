from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle

from table_review.documents.types import PageBox
from table_review.extraction.backends import extract_with_backend
from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.native_fast import NativeFastExtractor
from table_review.extraction.types import ExtractionRequest, ExtractorConfig
from table_review.tables.auto_subtotals import detect_subtotals
from tests.fixtures.pdf_factory import two_page_report


def _currency_and_percent_report(path: Path) -> Path:
    canvas = Canvas(str(path), pagesize=(612, 792), invariant=1)
    canvas.drawString(60, 745, "Quarterly Revenue Summary ($ in thousands)")
    table = Table(
        [
            ["Segment", "2025", "2024", "YoY"],
            ["Cloud", "$800.00", "$600.00", "33.3%"],
            ["Support", "$200.00", "$200.00", "0.0%"],
            ["Total", "$1,000.00", "$800.00", "25.0%"],
        ],
        colWidths=[160, 100, 100, 80],
        rowHeights=[28] * 4,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 11),
            ]
        )
    )
    table.wrapOn(canvas, 500, 500)
    table.drawOn(canvas, 60, 560)
    canvas.save()
    return path


def test_native_fast_extracts_financial_table_captions_and_subtotals(tmp_path):
    source = two_page_report(tmp_path / "fixture.pdf")
    result = NativeFastExtractor().extract(ExtractionRequest(source_path=source, pages=(2,)))
    assert result.manifest["backend"] == "native_fast"
    assert result.manifest["confident"] is True
    assert result.manifest["conversion_outcome"] == "success"
    assert len(result.tables) == 1

    table = result.tables[0]
    content = table.content
    assert (content.rows, content.columns) == (4, 3)
    assert content.header_rows == (0,)
    assert content.row_label_columns == (0,)
    assert any("USD millions" in src.text for src in content.context_sources)

    by_slot = {(c.row, c.col): c for c in content.cells}
    assert by_slot[(1, 1)].text == "1,234.50"
    assert by_slot[(1, 1)].interpretation == "en_us_number"
    assert by_slot[(1, 1)].reference.precision == "cell"
    assert by_slot[(2, 1)].text == "(123.45)"
    assert by_slot[(2, 1)].interpretation == "en_us_number"
    assert by_slot[(3, 1)].text == "1,111.05"

    detected = detect_subtotals(content)
    assert len(detected) == 2
    assert all(item.state == "match" for item in detected)


def test_native_fast_handles_currency_percent_and_region_crop(tmp_path):
    source = _currency_and_percent_report(tmp_path / "segments.pdf")
    full = NativeFastExtractor().extract(ExtractionRequest(source_path=source, pages=(1,)))
    assert len(full.tables) == 1
    content = full.tables[0].content
    assert (content.rows, content.columns) == (4, 4)

    by_slot = {(c.row, c.col): c for c in content.cells}
    assert by_slot[(1, 1)].text == "$800.00"
    assert by_slot[(1, 1)].interpretation == "en_us_number"
    assert by_slot[(1, 3)].text == "33.3%"
    assert by_slot[(1, 3)].interpretation == "percent"
    assert by_slot[(3, 1)].text == "$1,000.00"

    detected = detect_subtotals(content)
    assert [d.state for d in detected] == ["match", "match"]

    # Explicit region covering the extracted table extracts cleanly too.
    region = PageBox(
        page=1,
        left=50,
        top=100,
        right=520,
        bottom=260,
        page_width=612,
        page_height=792,
    )
    cropped = NativeFastExtractor().extract(
        ExtractionRequest(source_path=source, pages=(1,), region=region)
    )
    assert len(cropped.tables) == 1
    assert cropped.tables[0].content.rows == 4


def test_auto_backend_routes_confident_digital_pages_without_docling(tmp_path, monkeypatch):
    source = two_page_report(tmp_path / "auto.pdf")

    def boom(*args, **kwargs):
        raise AssertionError("Docling should not run for confident digital pages")

    monkeypatch.setattr(DoclingExtractor, "extract", boom)
    result = extract_with_backend(
        ExtractionRequest(
            source_path=source,
            pages=(2,),
            config=ExtractorConfig(backend="auto", artifacts_path=tmp_path / "missing-models"),
        )
    )
    assert result.manifest["backend"] == "auto"
    assert result.manifest["backends_used"] == ["native_fast"]
    assert len(result.tables) == 1
    assert result.tables[0].content.rows == 4
