import pytest

from table_review.extraction.ocr import ocr_options
from table_review.extraction.types import ExtractorConfig

pytestmark = pytest.mark.extraction


def test_english_report_ocr_uses_the_pinned_english_recognizer_offline(tmp_path):
    root = tmp_path / "rapidocr"
    root.mkdir()
    for name in (
        "PP-OCRv6_det_small.onnx",
        "PP-OCRv6_rec_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "en_PP-OCRv5_rec_mobile.onnx",
    ):
        (root / name).write_bytes(b"fixture")
    options = ocr_options(ExtractorConfig(artifacts_path=tmp_path))
    assert options.lang == ["en"]
    assert options.rec_model_path == str(root / "en_PP-OCRv5_rec_mobile.onnx")
    assert options.det_model_path == str(root / "PP-OCRv6_det_small.onnx")
    assert options.rapidocr_params["Rec.lang_type"].value == "en"
    assert options.rapidocr_params["Rec.ocr_version"].value == "PP-OCRv5"
    assert options.rapidocr_params["Rec.model_type"].value == "mobile"


def test_missing_english_recognizer_fails_without_falling_back_or_downloading(tmp_path):
    from table_review.documents.errors import DocumentError

    root = tmp_path / "rapidocr"
    root.mkdir()
    for name in (
        "PP-OCRv6_det_small.onnx",
        "PP-OCRv6_rec_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    ):
        (root / name).write_bytes(b"fixture")
    with pytest.raises(DocumentError, match="Offline OCR models"):
        ocr_options(ExtractorConfig(artifacts_path=tmp_path))
