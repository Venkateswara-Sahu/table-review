"""Keep non-painting PDF text out of layout and cell-matching inputs.

This uses the pinned Docling native parser's rendering-mode metadata, without
rewriting source bytes, inferring text, or altering page geometry.
"""

from docling.backend.docling_parse_backend import (
    ThreadedDoclingParseDocumentBackend,
    ThreadedDoclingParsePageBackend,
)
from docling_core.types.doc.page import PdfCellRenderingMode, PdfTextCell

_HIDDEN = {PdfCellRenderingMode.INVISIBLE, PdfCellRenderingMode.ONLY_CLIPPING}


class VisiblePdfPage(ThreadedDoclingParsePageBackend):
    def get_segmented_page(self):
        page = super().get_segmented_page()
        if page is not None:
            for field in ("char_cells", "word_cells", "textline_cells"):
                setattr(
                    page,
                    field,
                    [
                        cell
                        for cell in getattr(page, field)
                        if not isinstance(cell, PdfTextCell) or cell.rendering_mode not in _HIDDEN
                    ],
                )
        return page


class VisiblePdfBackend(ThreadedDoclingParseDocumentBackend):
    def iter_pages(self):
        self._iterating = True
        try:
            for result in self.parser.iterate_results():
                yield VisiblePdfPage(result, rendered=self._render_pages)
        finally:
            self._iterating = False
