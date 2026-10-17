"""Locate isolated horizontal marks in unrecognized OCR cells.

Estimated cell windows only guide inspection. Precise evidence is the actual
ink component; a recognizer must read it before a provisional literal is added.
"""

from statistics import median

from table_review.documents.types import ImmutableModel, PageBox
from table_review.tables.types import TableContent


class MarkCandidate(ImmutableModel):
    cell_id: str
    box: PageBox
    window: PageBox


def dash_candidates(content: TableContent, image, scale: float, *, limit=64):
    import cv2
    import numpy as np

    if not 0 <= limit <= 64 or scale <= 0:
        raise ValueError("Invalid bounded mark inspection")
    reference = content.source_reference
    if reference is None or not limit:
        return (), 0
    anchors = [
        cell
        for cell in content.cells
        if cell.text.strip()
        and cell.row_span == cell.col_span == 1
        and cell.reference.precision == "cell"
        and cell.reference.box is not None
        and cell.role not in {"column_header", "row_header", "section", "label", "header"}
    ]
    if anchors:
        dimensions = anchors[0].reference.box
        if (
            abs(image.width - dimensions.page_width * scale) > 1
            or abs(image.height - dimensions.page_height * scale) > 1
        ):
            return (), 0
    candidates, inspected = [], 0
    for cell in content.cells:
        if cell.text.strip() or cell.row_span != 1 or cell.col_span != 1:
            continue
        if inspected >= limit:
            break
        inspected += 1
        row = [c.reference.box for c in anchors if c.row == cell.row and c.col > 0]
        column = [c.reference.box for c in anchors if c.col == cell.col]
        if not row or len(column) < 2:
            continue
        window = PageBox(
            page=reference.page,
            left=max(0, median(b.left for b in column) - 2),
            right=min(column[0].page_width, median(b.right for b in column) + 2),
            top=max(0, median(b.top for b in row) - 1),
            bottom=min(row[0].page_height, median(b.bottom for b in row) + 1),
            page_width=column[0].page_width,
            page_height=row[0].page_height,
        )
        x, y, right, bottom = (
            round(v * scale) for v in (window.left, window.top, window.right, window.bottom)
        )
        if right <= x or bottom <= y or right > image.width or bottom > image.height:
            continue
        gray = np.asarray(image.crop((x, y, right, bottom)).convert("L"))
        mask = (gray < 220).astype(np.uint8)
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        components = [
            tuple(map(int, stat))
            for stat in stats[1:]
            if stat[4] >= max(2, scale * scale * 0.2)
            and 0.16 < (stat[1] + stat[3] / 2) / (bottom - y) < 0.84
        ]
        if len(components) != 1:
            continue
        left_px, top_px, width, height, _ = components[0]
        if not (
            width >= 2.5 * scale
            and width >= 4 * height
            and width <= min(20 * scale, (right - x) * 0.8)
            and height <= (bottom - y) * 0.25
        ):
            continue
        box = PageBox(
            page=reference.page,
            left=(x + left_px) / scale,
            top=(y + top_px) / scale,
            right=(x + left_px + width) / scale,
            bottom=(y + top_px + height) / scale,
            page_width=window.page_width,
            page_height=window.page_height,
        )
        candidates.append(MarkCandidate(cell_id=cell.id, box=box, window=window))
    return tuple(candidates), inspected
