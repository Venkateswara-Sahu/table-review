import hashlib
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader

from table_review.documents import pdf_native
from table_review.documents.pdf_native import page as _page
from table_review.documents.pdf_native import serialized_pdfium as _serialized_pdfium
from table_review.documents.pdf_native import visible_char as _visible_char
from table_review.documents.types import PageBox, PageInfo, PdfInfo, RenderedPage, SourceReference
from table_review.settings import Settings


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


def page_info(path: Path, page_no: int) -> PageInfo:
    return PageInfo.model_validate(pdf_native.page_info(path, page_no))


def render_page(path: Path, page_no: int, scale: float) -> RenderedPage:
    return RenderedPage.model_validate(
        pdf_native.render_page(path, page_no, scale, Settings().max_render_pixels)
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
