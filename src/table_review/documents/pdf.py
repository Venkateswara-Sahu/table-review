import hashlib
import math
import tempfile
from contextlib import closing
from functools import wraps
from pathlib import Path
from threading import RLock

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from pypdf import PdfReader

from table_review.documents.types import PageBox, PageInfo, PdfInfo, RenderedPage, SourceReference
from table_review.settings import Settings

_PDFIUM_LOCK = RLock()


def _visible_char(text, index):
    obj = pdfium_raw.FPDFText_GetTextObject(text, index)
    # Generated whitespace may have no text object; it is never positive evidence.
    return bool(obj) and pdfium_raw.FPDFTextObj_GetTextRenderMode(obj) not in (3, 7)


def _serialized_pdfium(function):
    @wraps(function)
    def call(*args, **kwargs):
        with _PDFIUM_LOCK:
            return function(*args, **kwargs)

    return call


def inspect_pdf(path: Path) -> PdfInfo:
    size = path.stat().st_size
    if size > Settings().max_pdf_bytes:
        raise ValueError("PDF exceeds the 50 MiB limit")
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        return PdfInfo(page_count=0, byte_count=size, encrypted=True)
    count = len(reader.pages)
    if count > Settings().max_pdf_pages:
        raise ValueError("PDF exceeds the 500 page limit")
    return PdfInfo(page_count=count, byte_count=size, encrypted=False)


def _page(document, page_no: int):
    if not 1 <= page_no <= len(document):
        raise ValueError("Page number outside this PDF")
    return document[page_no - 1]


@_serialized_pdfium
def page_info(path: Path, page_no: int) -> PageInfo:
    with pdfium.PdfDocument(path) as document, closing(_page(document, page_no)) as page:
        width, height = page.get_size()
        with closing(page.get_textpage()) as text:
            digital = any(
                _visible_char(text, i) and text.get_text_range(i, 1).strip()
                for i in range(text.count_chars())
            )
        return PageInfo(page_no=page_no, width=width, height=height, digital_text_present=digital)


@_serialized_pdfium
def render_page(path: Path, page_no: int, scale: float) -> RenderedPage:
    if not 0 < scale <= 4:
        raise ValueError("Render scale must be greater than zero and at most four")
    with pdfium.PdfDocument(path) as document, closing(_page(document, page_no)) as page:
        width, height = page.get_size()
        if math.ceil(width * scale) * math.ceil(height * scale) > Settings().max_render_pixels:
            raise ValueError("Page render exceeds the pixel budget")
        bitmap = page.render(scale=scale)
        try:
            image = bitmap.to_pil()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as output:
                image_path = Path(output.name)
            image.save(image_path)
            pixel_width, pixel_height = image.size
        finally:
            bitmap.close()
    return RenderedPage(
        image_path=image_path,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
        page_box=PageBox(
            page=page_no,
            left=0,
            top=0,
            right=width,
            bottom=height,
            page_width=width,
            page_height=height,
        ),
    )


@_serialized_pdfium
def source_tokens(path: Path, page_no: int):
    from table_review.extraction.types import SourceToken

    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    tokens = []
    with pdfium.PdfDocument(path) as document, closing(_page(document, page_no)) as page:
        width, height = page.get_size()
        crop_l, crop_b, crop_r, crop_t = page.get_bbox()
        unrotated_w, unrotated_h = crop_r - crop_l, crop_t - crop_b
        rotation = page.get_rotation()
        with closing(page.get_textpage()) as textpage:
            chars, boxes, start = [], [], 0

            def flush():
                if not chars or not boxes:
                    return
                l = max(0, min(b[0] for b in boxes))
                t = max(0, min(b[1] for b in boxes))
                r = min(width, max(b[2] for b in boxes))
                b = min(height, max(b[3] for b in boxes))
                if l >= r or t >= b:
                    return
                box = PageBox(
                    page=page_no,
                    left=l,
                    top=t,
                    right=r,
                    bottom=b,
                    page_width=width,
                    page_height=height,
                )
                tokens.append(
                    SourceToken(
                        id=f"{source_hash}:{page_no}:{start}",
                        text="".join(chars),
                        box=box,
                        provenance=SourceReference(page=page_no, precision="cell", box=box),
                    )
                )

            for i in range(textpage.count_chars()):
                character = textpage.get_text_range(i, 1)
                if not _visible_char(textpage, i):
                    flush()
                    chars, boxes = [], []
                    continue
                if not character or character.isspace():
                    flush()
                    chars, boxes = [], []
                    continue
                if not chars:
                    start = i
                l, b, r, t = textpage.get_charbox(i)
                l, r, b, t = l - crop_l, r - crop_l, b - crop_b, t - crop_b
                if rotation == 0:
                    mapped = (l, unrotated_h - t, r, unrotated_h - b)
                elif rotation == 90:
                    mapped = (b, l, t, r)
                elif rotation == 180:
                    mapped = (unrotated_w - r, b, unrotated_w - l, t)
                elif rotation == 270:
                    mapped = (unrotated_h - t, unrotated_w - r, unrotated_h - b, unrotated_w - l)
                else:
                    raise ValueError("Unsupported PDF rotation")
                chars.append(character)
                boxes.append(mapped)
            flush()
    return tuple(tokens)
