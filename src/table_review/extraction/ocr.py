"""Offline OCR assets and image-grounded source tokens."""

from table_review.documents.errors import DocumentError
from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.types import SourceToken


def ocr_options(config):
    from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions
    from rapidocr import LangRec, ModelType, OCRVersion

    root = config.artifacts_path.resolve() / "rapidocr"
    names = {
        "det_model_path": "PP-OCRv6_det_small.onnx",
        "rec_model_path": "en_PP-OCRv5_rec_mobile.onnx",
        "cls_model_path": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    }
    if not all((root / name).is_file() for name in names.values()):
        raise DocumentError("ocr_unavailable", "Offline OCR models are not available")
    return RapidOcrOptions(
        backend="onnxruntime",
        lang=["en"],
        mode=OcrMode.FULL_PAGE,
        **{key: str(root / name) for key, name in names.items()},
        rapidocr_params={
            # Explicit model paths bypass Docling's recognizer metadata setup.
            # Keep the language/backbone paired with the preloaded English file.
            "Rec.lang_type": LangRec.EN,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "EngineConfig.onnxruntime.intra_op_num_threads": config.num_threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        },
    )


def mark_reader(config):
    from pathlib import Path

    import rapidocr
    from rapidocr import LangRec, ModelType, OCRVersion
    from rapidocr.ch_ppocr_rec import TextRecognizer
    from rapidocr.utils.log import logger
    from rapidocr.utils.parse_parameters import ParseParams

    english = config.artifacts_path.resolve() / "rapidocr/en_PP-OCRv5_rec_mobile.onnx"
    if not english.is_file():
        raise DocumentError(
            "ocr_unavailable", "Offline English mark-recognition model is unavailable"
        )
    # RapidOCR initializes all three inference sessions even with detection/rotation
    # disabled at call time. This path only recognizes already-grounded small crops.
    cfg = ParseParams.update_batch(
        ParseParams.load(Path(rapidocr.__file__).with_name("config.yaml")),
        {
            "Rec.model_path": str(english),
            "Rec.lang_type": LangRec.EN,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": config.num_threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.use_cuda": False,
        },
    )
    cfg.Rec.engine_cfg = cfg.EngineConfig[cfg.Rec.engine_type.value]
    cfg.Rec.font_path = cfg.Global.font_path
    cfg.Rec.model_root_dir = english.parent
    logger.setLevel(cfg.Global.log_level.upper())
    return _MarkReader(TextRecognizer(cfg.Rec), cfg.Global)


class _MarkReader:
    def __init__(self, recognizer, config):
        from rapidocr.utils.load_image import LoadImage

        self.recognizer = recognizer
        self.config = config
        self.load_image = LoadImage()

    def __call__(self, image, *, use_det=False, use_cls=False):
        from rapidocr.ch_ppocr_rec import TextRecInput
        from rapidocr.utils.process_img import resize_image_within_bounds

        if use_det or use_cls:
            raise ValueError("Mark reader supports recognition-only crops")
        image = self.load_image(image)
        # Match the pinned RapidOCR recognition-only image loading/resizing path.
        # Vertical padding and rotation belong to detection and remain disabled.
        if self.config.use_preprocess_img:
            image, _, _ = resize_image_within_bounds(
                image, self.config.min_side_len, self.config.max_side_len
            )
        return self.recognizer(TextRecInput(img=[image], return_word_box=False))


def ocr_tokens(result, page, backend_page, source_hash, width, height, offset_left, offset_top):
    doc_page = result.document.pages.get(backend_page)
    if doc_page is None or not (
        abs(doc_page.size.width - width) < 0.1 and abs(doc_page.size.height - height) < 0.1
    ):
        return (), (f"Page {page.page_no}: OCR token geometry unavailable; precise tokens omitted",)
    tokens = []
    for parsed in result.pages:
        if parsed.page_no != backend_page or parsed.parsed_page is None:
            continue
        for index, cell in enumerate(parsed.parsed_page.textline_cells):
            if not cell.from_ocr or not cell.text.strip():
                continue
            bbox = cell.rect.to_bounding_box().to_top_left_origin(height)
            try:
                box = PageBox(
                    page=page.page_no,
                    left=max(0, bbox.l + offset_left),
                    top=max(0, bbox.t + offset_top),
                    right=min(page.width, bbox.r + offset_left),
                    bottom=min(page.height, bbox.b + offset_top),
                    page_width=page.width,
                    page_height=page.height,
                )
            except ValueError:
                continue
            tokens.append(
                SourceToken(
                    id=f"{source_hash}:{page.page_no}:ocr:{index}",
                    text=cell.text,
                    box=box,
                    provenance=SourceReference(page=page.page_no, precision="cell", box=box),
                )
            )
    return tuple(tokens), ()
