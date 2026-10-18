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


def _line_centres(indices):
    groups = []
    for index in indices:
        if groups and index - groups[-1][-1] <= 2:
            groups[-1].append(index)
        else:
            groups.append([index])
    return [group[len(group) // 2] for group in groups]


def _ruled_table_image(obj):
    """Cheap bounded grid evidence for small images sharing native-text pages.

    A 3-by-3 set of crossing rules can enclose a 2-by-2 table. This routes an
    optional parser pass; it does not recognize a table or certify its values.
    Borderless small images remain available through manual region selection.
    """
    width, height = obj.get_px_size()
    if width < 160 or height < 64 or width * height > 4_000_000:
        return False
    try:
        bitmap = obj.get_bitmap()
        try:
            with bitmap.to_pil() as source:
                image = source.convert("L")
        finally:
            # The PIL buffer view is closed; convert() made the independent
            # grayscale copy used below, so no view survives this release.
            bitmap.warn_on_close = False
            bitmap.close()
    except (pdfium.PdfiumError, ValueError):
        return False
    try:
        image.thumbnail((512, 512))
        width, height = image.size
        pixels = image.tobytes()
    finally:
        image.close()
    horizontal = _line_centres(
        [
            y
            for y in range(height)
            if sum(pixel < 144 for pixel in pixels[y * width : (y + 1) * width]) >= width * 0.45
        ]
    )
    if len(horizontal) < 3 or horizontal[-1] - horizontal[0] < height * 0.15:
        return False
    first, last = horizontal[0], horizontal[-1]
    vertical = _line_centres(
        [
            x
            for x in range(width)
            if sum(pixels[y * width + x] < 144 for y in range(first, last + 1))
            >= (last - first + 1) * 0.7
        ]
    )
    if len(vertical) < 3 or vertical[-1] - vertical[0] < width * 0.3:
        return False
    return (
        sum(
            sum(
                any(
                    pixels[near_y * width + near_x] < 144
                    for near_y in range(max(0, y - 1), min(height, y + 2))
                    for near_x in range(max(0, x - 1), min(width, x + 2))
                )
                for y in horizontal
            )
            >= 3
            for x in vertical
        )
        >= 3
    )


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
        image_area, small_grid_image, inspected_images = 0, False, 0
        for obj in sheet.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE]):
            left, bottom, right, top = obj.get_bounds()
            visible_width = max(0, min(right, crop_r) - max(left, crop_l))
            visible_height = max(0, min(top, crop_t) - max(bottom, crop_b))
            image_area += visible_width * visible_height
            if (
                readable_chars >= 20
                and not small_grid_image
                and image_area < width * height * 0.15
                and inspected_images < 8
                and visible_width >= 120
                and visible_height >= 48
            ):
                inspected_images += 1
                small_grid_image = _ruled_table_image(obj)
        requires_ocr = image_area >= width * height * 0.15 and readable_chars < 20
        mixed_image_content = readable_chars >= 20 and (
            image_area >= width * height * 0.15 or small_grid_image
        )
    return {
        "page_no": page_no,
        "width": width,
        "height": height,
        "digital_text_present": digital,
        "requires_ocr": requires_ocr,
        "mixed_image_content": mixed_image_content,
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
