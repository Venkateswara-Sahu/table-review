import pytest

from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.types import ExtractionRequest
from tests.domain.test_raster_pdf import raster_report


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
