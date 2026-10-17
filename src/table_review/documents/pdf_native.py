"""Small PDF preview primitives, without application or full-reader startup."""

import math
import tempfile
from contextlib import closing
from functools import wraps
from pathlib import Path
from threading import RLock

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

_PDFIUM_LOCK = RLock()


def visible_char(text, index):
    obj = pdfium_raw.FPDFText_GetTextObject(text, index)
    return bool(obj) and pdfium_raw.FPDFTextObj_GetTextRenderMode(obj) not in (3, 7)


def serialized_pdfium(function):
    @wraps(function)
    def call(*args, **kwargs):
        with _PDFIUM_LOCK:
            return function(*args, **kwargs)

    return call


def page(document, page_no):
    if not 1 <= page_no <= len(document):
        raise ValueError("Page number outside this PDF")
    return document[page_no - 1]


@serialized_pdfium
def page_info(path, page_no):
    with pdfium.PdfDocument(path) as document, closing(page(document, page_no)) as sheet:
        width, height = sheet.get_size()
        crop_l, crop_b, crop_r, crop_t = sheet.get_bbox()
        with closing(sheet.get_textpage()) as text:
            digital, readable_chars = False, 0
            for i in range(text.count_chars()):
                if not visible_char(text, i) or not text.get_text_range(i, 1).strip():
                    continue
                digital = True
                left, bottom, right, top = text.get_charbox(i)
                if top - bottom >= 3 and crop_b + (crop_t - crop_b) * 0.05 < bottom < crop_t:
                    readable_chars += 1
        # Preserve native text on mixed pages. Tiny footer search layers do not
        # count as usable content; routing remains a heuristic, not an accuracy score.
        image_area = 0
        for obj in sheet.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE]):
            left, bottom, right, top = obj.get_bounds()
            image_area += max(0, min(right, crop_r) - max(left, crop_l)) * max(
                0, min(top, crop_t) - max(bottom, crop_b)
            )
        requires_ocr = image_area >= width * height * 0.15 and readable_chars < 20
    return {
        "page_no": page_no,
        "width": width,
        "height": height,
        "digital_text_present": digital,
        "requires_ocr": requires_ocr,
    }


@serialized_pdfium
def render_page(path, page_no, scale, max_pixels=16_000_000):
    if not math.isfinite(scale) or not 0 < scale <= 4:
        raise ValueError("Render scale must be greater than zero and at most four")
    with pdfium.PdfDocument(path) as document, closing(page(document, page_no)) as sheet:
        width, height = sheet.get_size()
        if math.ceil(width * scale) * math.ceil(height * scale) > max_pixels:
            raise ValueError("Page render exceeds the pixel budget")
        bitmap = sheet.render(scale=scale)
        image_path = None
        try:
            image = bitmap.to_pil()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as output:
                image_path = Path(output.name)
            # Lossless pixels with less CPU spent compressing local previews.
            image.save(image_path, compress_level=1)
            pixel_width, pixel_height = image.size
        except BaseException:
            if image_path is not None:
                image_path.unlink(missing_ok=True)
            raise
        finally:
            bitmap.close()
    return {
        "image_path": str(image_path),
        "pixel_width": pixel_width,
        "pixel_height": pixel_height,
        "page_box": {
            "page": page_no,
            "left": 0,
            "top": 0,
            "right": width,
            "bottom": height,
            "page_width": width,
            "page_height": height,
        },
    }
