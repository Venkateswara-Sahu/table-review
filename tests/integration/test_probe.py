import json
from pathlib import Path

import pytest

from tests.fixtures.pdf_factory import rotated_cropped_report, two_page_report


def test_pdf_metadata_and_original_page(tmp_path: Path):
    from table_review.documents.pdf import inspect_pdf, render_page

    source = two_page_report(tmp_path / "report.pdf")
    info = inspect_pdf(source)
    assert info.page_count == 2 and not info.encrypted
    rendered = render_page(source, 2, 1.0)
    assert rendered.page_box.page == 2
    assert rendered.pixel_width == 612 and rendered.pixel_height == 792


@pytest.mark.extraction
def test_probe_preserves_original_page(tmp_path: Path):
    from table_review.extraction.docling_adapter import DoclingExtractor
    from table_review.extraction.types import ExtractionRequest, ExtractorConfig

    source = two_page_report(tmp_path / "report.pdf")
    request = ExtractionRequest(source_path=source, pages=(2,), config=ExtractorConfig())
    result = DoclingExtractor().extract(request)
    assert result.tables and all(t.page_no == 2 for t in result.tables)
    assert result.manifest["device"] == "cpu"
    assert result.manifest["model_artifact_hashes"]
    assert any("1,234.50" in c.raw_text for t in result.tables for c in t.content.cells)
    assert all(c.reference.page == 2 for t in result.tables for c in t.content.cells)
    expected_headers = tuple(
        sorted(
            {
                r
                for cell in result.tables[0].content.cells
                if cell.role == "column_header"
                for r in range(cell.row, cell.row + cell.row_span)
            }
        )
    )
    assert result.tables[0].content.header_rows == expected_headers


def test_rotated_crop_tokens_match_display(tmp_path: Path):
    from table_review.documents.pdf import render_page, source_tokens

    path = rotated_cropped_report(tmp_path / "crop.pdf")
    rendered = render_page(path, 1, 1.0)
    assert (rendered.pixel_width, rendered.pixel_height) == (260, 460)
    tokens = source_tokens(path, 1)
    revenue = next(token for token in tokens if token.text == "Revenue")
    # Original x=66, y~615: clockwise rotation puts it at display x~115, y~26.
    assert 100 < revenue.box.left < 140
    assert 20 < revenue.box.top < 40
    assert revenue.box.page == 1


@pytest.mark.extraction
def test_selected_region_is_clipped_and_reversible(tmp_path: Path):
    from table_review.documents.types import PageBox
    from table_review.extraction.docling_adapter import DoclingExtractor
    from table_review.extraction.types import ExtractionRequest

    path = two_page_report(tmp_path / "report.pdf")
    region = PageBox(
        page=2, left=55, top=115, right=485, bottom=260, page_width=612, page_height=792
    )
    result = DoclingExtractor().extract(
        ExtractionRequest(source_path=path, pages=(2,), region=region)
    )
    assert result.tables
    assert result.manifest["region_transform"]["offset_top"] == 115
    for table in result.tables:
        assert table.page_no == 2
        assert table.region and region.left <= table.region.left < region.right
        assert region.top <= table.region.top < region.bottom
        assert all("USD" not in token.text for token in table.source_tokens)
    assert any(
        "1,234.50" in cell.raw_text for table in result.tables for cell in table.content.cells
    )


@pytest.mark.extraction
def test_cropped_pdf_precise_geometry_is_validated(tmp_path: Path):
    from pypdf import PdfReader, PdfWriter

    from table_review.extraction.docling_adapter import DoclingExtractor
    from table_review.extraction.types import ExtractionRequest

    source = two_page_report(tmp_path / "report.pdf")
    reader = PdfReader(source)
    page = reader.pages[1]
    page.cropbox.lower_left = (40, 40)
    page.cropbox.upper_right = (540, 760)
    writer = PdfWriter()
    writer.add_page(page)
    path = tmp_path / "cropped.pdf"
    writer.write(path)
    result = DoclingExtractor().extract(ExtractionRequest(source_path=path, pages=(1,)))
    revenue = next(c for t in result.tables for c in t.content.cells if c.text == "Revenue")
    assert revenue.reference.precision == "cell"
    box = revenue.reference.box
    assert box and 15 < box.left < 40 and 125 < box.top < 160


@pytest.mark.extraction
def test_probe_writes_actual_results_and_rejects_changed_source(tmp_path: Path):
    import hashlib

    from table_review.extraction.probe import run_probe

    path = two_page_report(tmp_path / "report.pdf")
    manifest = tmp_path / "development.json"
    payload = {
        "cases": [
            {
                "id": "fixture",
                "source_path": str(path),
                "page": 2,
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "anchor_literals": ["1,234.50", "Revenue"],
            }
        ],
        "configs": [{"cell_matching": True}],
    }
    manifest.write_text(json.dumps(payload))
    summary = run_probe(manifest, tmp_path / "output")
    assert summary.case_count == 1 and summary.failures == 0
    results = json.loads(summary.results_path.read_text())
    result = results["results"][0]
    assert result["anchor_literals_correct"] == 2
    assert result["peak_rss_bytes"] > 0 and result["elapsed_seconds"] > 0
    assert result["precise_cell_boxes"] > 0
    payload["cases"][0]["source_sha256"] = "changed"
    manifest.write_text(json.dumps(payload))
    summary = run_probe(manifest, tmp_path / "changed")
    assert summary.failures == 1
    assert (
        json.loads(summary.results_path.read_text())["results"][0]["error_type"]
        == "source_hash_mismatch"
    )


def test_render_rejects_excessive_pixels_before_allocation(tmp_path: Path, monkeypatch):
    import pypdfium2 as pdfium
    from pypdf import PdfWriter

    from table_review.documents.pdf import render_page

    path = tmp_path / "huge-page.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100000, height=100000)
    writer.write(path)

    def forbidden(*args, **kwargs):
        raise RuntimeError("Unsafe native allocation reached")

    monkeypatch.setattr(pdfium.PdfPage, "render", forbidden)
    with pytest.raises(ValueError, match="pixel"):
        render_page(path, 1, 1.0)


def test_native_pdf_calls_do_not_overlap_between_api_threads(tmp_path: Path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event, Lock

    import pypdfium2 as pdfium

    from table_review.documents.pdf import render_page

    path = two_page_report(tmp_path / "report.pdf")
    actual = pdfium.PdfDocument
    entered, release, second_started = Event(), Event(), Event()
    counter = Lock()
    active = [0]

    class TrackedDocument:
        def __init__(self, *args, **kwargs):
            with counter:
                if active[0]:
                    raise RuntimeError("Native calls overlapped")
                active[0] += 1
            self.document = actual(*args, **kwargs)

        def __enter__(self):
            if not entered.is_set():
                entered.set()
                assert release.wait(5)
            return self.document

        def __exit__(self, *args):
            self.document.close()
            with counter:
                active[0] -= 1

    monkeypatch.setattr(pdfium, "PdfDocument", TrackedDocument)

    def second():
        second_started.set()
        return render_page(path, 2, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(render_page, path, 2, 1)
        assert entered.wait(5)
        follow = pool.submit(second)
        assert second_started.wait(5)
        try:
            with pytest.raises(TimeoutError):
                follow.result(timeout=0.05)
        finally:
            release.set()
        assert first.result().pixel_width == follow.result().pixel_width == 612
