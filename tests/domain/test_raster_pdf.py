from reportlab.pdfgen.canvas import Canvas

from table_review.documents.pdf import page_info, render_page
from tests.fixtures.pdf_factory import two_page_report


def small_mixed_image_report(tmp_path, *, kind="table"):
    """Native prose plus a 4.6%-area raster; table pixels are a ruled 2x2."""
    image_source = tmp_path / f"{kind}-source.pdf"
    image_canvas = Canvas(str(image_source), pagesize=(250, 90), invariant=1)
    image_canvas.setFont("Helvetica", 12)
    if kind == "table":
        for x in (5, 150, 245):
            image_canvas.line(x, 5, x, 85)
        for y in (5, 45, 85):
            image_canvas.line(5, y, 245, y)
        for label, number, y in (("Revenue", "100", 60), ("Expense", "20", 20)):
            image_canvas.drawString(15, y, label)
            image_canvas.drawString(165, y, number)
    elif kind == "prose":
        for y, text in (
            (65, "Please review this report."),
            (45, "All figures are preliminary."),
            (25, "Further details follow below."),
        ):
            image_canvas.drawString(5, y, text)
    else:
        image_canvas.rect(5, 5, 240, 80)
        image_canvas.drawString(45, 40, "ACME REPORTS")
    image_canvas.save()
    rendered = render_page(image_source, 1, 2)
    path = tmp_path / f"small-mixed-{kind}.pdf"
    canvas = Canvas(str(path), pagesize=(612, 792), invariant=1)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(30, 750, "Native report commentary stays selectable and unchanged.")
    canvas.drawImage(str(rendered.image_path), 30, 500, width=250, height=90)
    canvas.save()
    rendered.image_path.unlink()
    return path


def test_small_ruled_raster_table_routes_a_native_prose_page_to_mixed_recovery(tmp_path):
    info = page_info(small_mixed_image_report(tmp_path), 1)
    assert info.digital_text_present and not info.requires_ocr
    assert info.mixed_image_content


def test_small_raster_prose_does_not_start_a_mixed_table_pass(tmp_path):
    info = page_info(small_mixed_image_report(tmp_path, kind="prose"), 1)
    assert info.digital_text_present and not info.requires_ocr
    assert not info.mixed_image_content


def test_small_logo_does_not_start_a_mixed_table_pass(tmp_path):
    info = page_info(small_mixed_image_report(tmp_path, kind="logo"), 1)
    assert info.digital_text_present and not info.requires_ocr
    assert not info.mixed_image_content


def raster_report(tmp_path, *, misplaced_text=False):
    original = two_page_report(tmp_path / "digital.pdf")
    rendered = render_page(original, 2, 2)
    path = tmp_path / "raster.pdf"
    canvas = Canvas(str(path), pagesize=(612, 792), invariant=1)
    canvas.drawImage(str(rendered.image_path), 0, 0, width=612, height=792)
    if misplaced_text:
        canvas.setFont("Helvetica", 1)
        canvas.drawString(30, 14, "Revenue 1,234.50 Expense Total")
    canvas.save()
    rendered.image_path.unlink()
    return path


def mixed_report(tmp_path):
    original = two_page_report(tmp_path / "native-source.pdf")
    rendered = render_page(original, 2, 2)
    path = tmp_path / "mixed-content.pdf"
    canvas = Canvas(str(path), pagesize=(612, 792), invariant=1)
    canvas.drawImage(str(rendered.image_path), 0, 0, width=612, height=600)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(30, 750, "Native report commentary remains selectable and unchanged.")
    canvas.drawString(30, 725, "The following table is embedded as a page image.")
    canvas.save()
    rendered.image_path.unlink()
    return path


def test_page_image_with_misplaced_text_requires_ocr(tmp_path):
    info = page_info(raster_report(tmp_path, misplaced_text=True), 1)
    assert info.digital_text_present
    assert info.requires_ocr
    assert not info.mixed_image_content


def test_normal_digital_table_keeps_native_extraction(tmp_path):
    info = page_info(two_page_report(tmp_path / "report.pdf"), 2)
    assert info.digital_text_present
    assert not info.requires_ocr
    assert not info.mixed_image_content


def test_digital_content_with_large_image_keeps_native_text(tmp_path):
    rendered = render_page(two_page_report(tmp_path / "source.pdf"), 2, 1)
    path = tmp_path / "mixed.pdf"
    canvas = Canvas(str(path), pagesize=(612, 792), invariant=1)
    canvas.drawImage(str(rendered.image_path), 0, 0, width=612, height=300)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(30, 700, "Revenue 1,234.50 Expense 200.00 Total 1,034.50")
    canvas.save()
    rendered.image_path.unlink()
    assert not page_info(path, 1).requires_ocr
    assert page_info(path, 1).mixed_image_content

    from pypdf import PdfReader

    from table_review.documents.types import PageBox
    from table_review.extraction.docling_adapter import _vector_page

    info = page_info(path, 1)
    region = PageBox(
        page=1,
        left=0,
        top=0,
        right=612,
        bottom=150,
        page_width=612,
        page_height=792,
    )
    normalized = tmp_path / "native-region.pdf"
    _vector_page(PdfReader(path), info, region, normalized)
    assert not page_info(normalized, 1).requires_ocr
    image_region = region.model_copy(update={"top": 492, "bottom": 792})
    _vector_page(PdfReader(path), info, image_region, normalized)
    assert page_info(normalized, 1).requires_ocr


def test_small_raster_table_can_be_selected_for_ocr(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from table_review.extraction.types import ExtractionRequest, ExtractorConfig
    from table_review.runtime import write_model_manifest

    rendered = render_page(two_page_report(tmp_path / "source.pdf"), 2, 1)
    path = tmp_path / "small-raster.pdf"
    canvas = Canvas(str(path), pagesize=(612, 792), invariant=1)
    canvas.drawImage(str(rendered.image_path), 20, 400, width=183, height=237)
    canvas.save()
    rendered.image_path.unlink()
    info = page_info(path, 1)
    assert not info.digital_text_present and not info.requires_ocr
    models = tmp_path / "models"
    models.mkdir()
    (models / "weights.pt").write_bytes(b"fixture")
    write_model_manifest(models)
    modes = []

    class Converter:
        def convert(self, path, **kwargs):
            return SimpleNamespace(
                status="success",
                errors=[],
                confidence=None,
                pages=[],
                document=SimpleNamespace(
                    tables=[],
                    pages={1: SimpleNamespace(size=SimpleNamespace(width=183, height=237))},
                ),
            )

    def converter(self, config, use_ocr=False):
        modes.append(use_ocr)
        return Converter()

    from table_review.documents.types import PageBox
    from table_review.extraction.docling_adapter import DoclingExtractor

    monkeypatch.setattr(DoclingExtractor, "_converter", converter)
    result = DoclingExtractor().extract(
        ExtractionRequest(
            source_path=path,
            pages=(1,),
            config=ExtractorConfig(artifacts_path=models),
            region=PageBox(
                page=1, left=20, top=155, right=203, bottom=392, page_width=612, page_height=792
            ),
        )
    )
    assert modes == [True]
    assert result.manifest["ocr_pages"] == [1]
