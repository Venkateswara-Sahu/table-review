import pytest

from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.types import ExtractionRequest
from tests.domain.test_raster_pdf import mixed_report, raster_report


@pytest.mark.extraction
@pytest.mark.parametrize("misplaced_text", [False, True])
def test_offline_ocr_recovers_image_table_and_image_grounded_tokens(tmp_path, misplaced_text):
    source = raster_report(tmp_path, misplaced_text=misplaced_text)
    result = DoclingExtractor().extract(ExtractionRequest(source_path=source, pages=(1,)))
    assert result.manifest["conversion_outcome"] == "success"
    assert result.manifest["ocr_pages"] == [1]
    assert result.manifest["pipeline_options"]["enable_remote_services"] is False
    assert result.tables
    table = result.tables[0]
    assert any("Revenue" in cell.text for cell in table.content.cells)
    assert any("1,234.50" in cell.text for cell in table.content.cells)
    assert table.source_tokens
    assert all(":ocr:" in token.id for token in table.source_tokens)
    revenue = next(token for token in table.source_tokens if "Revenue" in token.text)
    assert 140 < revenue.box.top < 190
    assert any("offline OCR" in warning for warning in result.warnings)


@pytest.mark.extraction
def test_digital_only_mode_still_rejects_scan_before_loading_models(tmp_path):
    from table_review.extraction.types import ExtractorConfig

    source = raster_report(tmp_path)
    with pytest.raises(ValueError, match="digital text"):
        DoclingExtractor().extract(
            ExtractionRequest(
                source_path=source,
                pages=(1,),
                config=ExtractorConfig(ocr_mode="never"),
            )
        )


@pytest.mark.extraction
def test_mixed_native_prose_and_image_table_recovers_automatically_and_by_region(tmp_path):
    from table_review.documents.pdf import page_info
    from table_review.documents.types import PageBox

    source = mixed_report(tmp_path)
    info = page_info(source, 1)
    assert info.digital_text_present and info.mixed_image_content and not info.requires_ocr
    full_page = DoclingExtractor().extract(ExtractionRequest(source_path=source, pages=(1,)))
    assert full_page.manifest["ocr_pages"] == [1]
    assert any(
        "1,234.50" in cell.text for table in full_page.tables for cell in table.content.cells
    )
    assert any("Revenue" in cell.text for table in full_page.tables for cell in table.content.cells)
    assert (
        full_page.manifest["page_outcomes"][0]["recovery_attempts"][0]["stage"] == "mixed_page_ocr"
    )
    assert any("select its region for offline OCR" in w for w in full_page.warnings)
    region = PageBox(
        page=1, left=0, top=192, right=612, bottom=792, page_width=612, page_height=792
    )
    result = DoclingExtractor().extract(
        ExtractionRequest(source_path=source, pages=(1,), region=region)
    )
    assert result.manifest["ocr_pages"] == [1]
    assert result.manifest["conversion_outcome"] == "success"
    assert any("1,234.50" in cell.text for table in result.tables for cell in table.content.cells)
    assert any("Revenue" in cell.text for table in result.tables for cell in table.content.cells)
    assert all(
        token.box.page == 1 and token.box.top >= region.top
        for table in result.tables
        for token in table.source_tokens
    )


@pytest.mark.extraction
def test_small_ruled_two_by_two_image_table_is_recovered_beside_native_prose(tmp_path):
    import hashlib

    from tests.domain.test_raster_pdf import small_mixed_image_report

    source = small_mixed_image_report(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = DoclingExtractor().extract(ExtractionRequest(source_path=source, pages=(1,)))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert result.manifest["ocr_pages"] == [1]
    assert any(t.content.rows == 2 and t.content.columns == 2 for t in result.tables)
    text = {c.text for t in result.tables for c in t.content.cells}
    assert {"Revenue", "100", "Expense", "20"} <= text
    assert all(t.page_no == 1 for t in result.tables)
