import pytest
from reportlab.pdfgen.canvas import Canvas

from table_review.documents.pdf import render_page
from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.types import ExtractionRequest


@pytest.mark.extraction
def test_image_chart_is_not_promoted_into_a_numeric_table(tmp_path):
    original = tmp_path / "chart.pdf"
    canvas = Canvas(str(original), pagesize=(612, 792), invariant=1)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(70, 700, "Quarterly revenue chart")
    canvas.line(80, 400, 80, 650)
    canvas.line(80, 400, 500, 400)
    canvas.setFont("Helvetica", 9)
    for value in range(0, 101, 20):
        canvas.drawString(50, 400 + value * 2, str(value))
    for index, value in enumerate((18, 40, 75, 95)):
        x = 120 + index * 90
        canvas.rect(x, 400, 35, value * 2, fill=0)
        canvas.drawString(x, 380, f"Q{index + 1}")
    canvas.save()
    rendered = render_page(original, 1, 2)
    raster = tmp_path / "raster-chart.pdf"
    canvas = Canvas(str(raster), pagesize=(612, 792), invariant=1)
    canvas.drawImage(str(rendered.image_path), 0, 0, width=612, height=792)
    canvas.save()
    rendered.image_path.unlink()
    result = DoclingExtractor().extract(ExtractionRequest(source_path=raster, pages=(1,)))
    assert result.tables == ()
    assert result.manifest["page_outcomes"][0]["recovery_attempts"] == []
    assert result.manifest["conversion_outcome"] == "success"
