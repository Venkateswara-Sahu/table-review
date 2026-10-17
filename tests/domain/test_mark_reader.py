from types import SimpleNamespace

import numpy as np
import pytest

from table_review.extraction.ocr import mark_reader
from table_review.extraction.types import ExtractorConfig


def test_mark_reader_only_initializes_recognition_and_preserves_crop_preprocessing(
    tmp_path, monkeypatch
):
    import rapidocr.ch_ppocr_rec as recognition
    from rapidocr import main

    root = tmp_path / "rapidocr"
    root.mkdir()
    english = root / "en_PP-OCRv5_rec_mobile.onnx"
    english.write_bytes(b"fixture")
    for name in (
        "PP-OCRv6_det_small.onnx",
        "PP-OCRv6_rec_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    ):
        (root / name).write_bytes(b"unused fixture")
    observed = {}

    def refuse(*args):
        pytest.fail("Mark recognition must not initialize detection or rotation models")

    class Recognizer:
        def __init__(self, config):
            observed["config"] = config

        def __call__(self, value):
            observed["input"] = value
            return SimpleNamespace(txts=("—",), scores=(0.75,))

    monkeypatch.setattr(main, "TextDetector", refuse)
    monkeypatch.setattr(main, "TextClassifier", refuse)
    monkeypatch.setattr(recognition, "TextRecognizer", Recognizer)
    reader = mark_reader(ExtractorConfig(artifacts_path=tmp_path, num_threads=2))
    image = np.full((10, 40, 3), 255, dtype=np.uint8)
    result = reader(image, use_det=False, use_cls=False)
    assert result.txts == ("—",) and result.scores == (0.75,)
    # The pinned library rounds the resized minimum dimension up to a multiple of 32.
    assert observed["input"].img[0].shape == (32, 128, 3)
    assert observed["input"].return_word_box is False
    assert observed["config"].model_path == str(english)
    assert observed["config"].engine_cfg.intra_op_num_threads == 2
    assert observed["config"].engine_cfg.inter_op_num_threads == 1
    assert observed["config"].engine_cfg.use_cuda is False


def test_mark_reader_missing_local_checkpoint_fails_closed(tmp_path):
    from table_review.documents.errors import DocumentError

    with pytest.raises(DocumentError, match="English mark-recognition"):
        mark_reader(ExtractorConfig(artifacts_path=tmp_path))
