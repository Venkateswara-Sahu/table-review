from pathlib import Path

import pytest
from reportlab.pdfgen.canvas import Canvas


def hidden_report(path: Path, *, visible=True):
    canvas = Canvas(str(path), invariant=1)
    for hidden in (False, True) if visible else (True,):
        text = canvas.beginText(60, 700)
        text.setTextRenderMode(3 if hidden else 0)
        text.textLine("Revenue 123.45")
        canvas.drawText(text)
    text = canvas.beginText(60, 650)
    text.setTextRenderMode(3)
    text.textLine("HIDDEN SECRET")
    canvas.drawText(text)
    canvas.save()
    return path


def test_hidden_duplicate_text_is_not_source_evidence(tmp_path):
    from table_review.documents.pdf import source_tokens

    tokens = source_tokens(hidden_report(tmp_path / "duplicate.pdf"), 1)
    assert [token.text for token in tokens] == ["Revenue", "123.45"]


def test_hidden_only_text_does_not_make_a_scan_supported(tmp_path):
    from table_review.documents.pdf import page_info

    assert not page_info(
        hidden_report(tmp_path / "scan.pdf", visible=False), 1
    ).digital_text_present


@pytest.mark.extraction
def test_real_native_backend_excludes_hidden_words_and_lines(tmp_path):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument

    from table_review.extraction.visible_backend import VisiblePdfBackend

    document = InputDocument(
        hidden_report(tmp_path / "native.pdf"), format=InputFormat.PDF, backend=VisiblePdfBackend
    )
    backend = document._backend
    try:
        page = next(backend.iter_pages())
        segmented = page.get_segmented_page()
        assert [cell.text for cell in segmented.word_cells] == ["Revenue", "123.45"]
        assert len(segmented.textline_cells) == 1
    finally:
        backend.unload()
