"""Fast deterministic extractor for born-digital PDF tables using pypdfium2 tokens."""

import hashlib
import re
import statistics
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psutil

from table_review.documents.pdf import inspect_pdf, page_info, source_tokens
from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.context import caption_sources
from table_review.extraction.structure import infer_aligned_row_labels, normalize_header_stubs
from table_review.extraction.timing import TimingLedger
from table_review.extraction.types import (
    ExtractedTable,
    ExtractionRequest,
    ExtractionResult,
    SourceToken,
)
from table_review.tables.grid import validate_grid
from table_review.tables.numbers import infer_interpretation
from table_review.tables.types import CellContent, TableContent, infer_header_roles

_CURRENCY_PREFIXES = frozenset({"$", "€", "£", "₹", "¥", "US$", "CA$", "A$", "HK$", "S$", "Rs", "Rs."})
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_NUMERIC_LIKE_START = re.compile(r"^[\(\-\−+]?\d|^[\-–—−]{1,2}$")
_PAGE_NUMBER_ONLY_RE = re.compile(r"^(?:page\s+)?\d+(?:\s+of\s+\d+)?$", re.IGNORECASE)


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _union_box(boxes: list[PageBox], page_no: int, width: float, height: float) -> PageBox:
    return PageBox(
        page=page_no,
        left=max(0.0, min(b.left for b in boxes)),
        top=max(0.0, min(b.top for b in boxes)),
        right=min(width, max(b.right for b in boxes)),
        bottom=min(height, max(b.bottom for b in boxes)),
        page_width=width,
        page_height=height,
    )


def _filter_tokens_for_region(
    tokens: tuple[SourceToken, ...], region: PageBox | None
) -> tuple[SourceToken, ...]:
    if region is None:
        return tokens
    selected = []
    for tok in tokens:
        cx = (tok.box.left + tok.box.right) / 2.0
        cy = (tok.box.top + tok.box.bottom) / 2.0
        if region.left - 1.0 <= cx <= region.right + 1.0 and region.top - 1.0 <= cy <= region.bottom + 1.0:
            selected.append(tok)
            continue
        iw = max(0.0, min(tok.box.right, region.right) - max(tok.box.left, region.left))
        ih = max(0.0, min(tok.box.bottom, region.bottom) - max(tok.box.top, region.top))
        area = max(1e-6, (tok.box.right - tok.box.left) * (tok.box.bottom - tok.box.top))
        if (iw * ih) / area >= 0.5:
            selected.append(tok)
    return tuple(selected)


def _group_phrases(tokens: tuple[SourceToken, ...], width: float, height: float, page_no: int):
    if not tokens:
        return [], 10.0, 5.0

    char_widths = [
        (t.box.right - t.box.left) / max(1, len(t.text))
        for t in tokens
        if len(t.text) >= 2 and t.box.right > t.box.left
    ]
    heights = [t.box.bottom - t.box.top for t in tokens if t.box.bottom > t.box.top]
    median_char_w = max(2.5, min(10.0, statistics.median(char_widths))) if char_widths else 5.0
    median_line_h = max(6.0, min(24.0, statistics.median(heights))) if heights else 10.0

    lines: list[dict] = []
    for tok in sorted(tokens, key=lambda item: ((item.box.top + item.box.bottom) / 2.0, item.box.left)):
        mid_y = (tok.box.top + tok.box.bottom) / 2.0
        tol = max(3.0, median_line_h * 0.55)
        if lines and abs(lines[-1]["mid_y"] - mid_y) <= tol:
            line = lines[-1]
            n = len(line["tokens"])
            line["mid_y"] = (line["mid_y"] * n + mid_y) / (n + 1)
            line["tokens"].append(tok)
        else:
            lines.append({"mid_y": mid_y, "tokens": [tok]})

    word_gap = min(max(median_char_w * 1.45, median_line_h * 0.72, 5.0), 14.0)
    currency_gap = max(median_line_h * 4.5, 48.0)

    phrase_rows = []
    for line in sorted(lines, key=lambda l: l["mid_y"]):
        row_tokens = sorted(line["tokens"], key=lambda item: item.box.left)
        phrases: list[dict] = []
        for tok in row_tokens:
            if not phrases:
                phrases.append({"tokens": [tok], "join_chars": []})
                continue
            prev = phrases[-1]
            prev_last = prev["tokens"][-1]
            gap = tok.box.left - prev_last.box.right
            prev_text = "".join(
                (prev["join_chars"][i - 1] if i > 0 else "") + pt.text
                for i, pt in enumerate(prev["tokens"])
            ).strip()
            if gap <= word_gap and gap >= -median_char_w * 1.5:
                joiner = "" if gap <= max(0.8, median_char_w * 0.18) else " "
                prev["join_chars"].append(joiner)
                prev["tokens"].append(tok)
            elif (
                prev_text in _CURRENCY_PREFIXES
                and _NUMERIC_LIKE_START.match(tok.text.strip())
                and 0.0 <= gap <= currency_gap
            ):
                prev["join_chars"].append("")
                prev["tokens"].append(tok)
            else:
                phrases.append({"tokens": [tok], "join_chars": []})

        row_items = []
        for phrase in phrases:
            toks = phrase["tokens"]
            text = "".join(
                (phrase["join_chars"][i - 1] if i > 0 else "") + pt.text
                for i, pt in enumerate(toks)
            ).strip()
            if not text:
                continue
            box = _union_box([t.box for t in toks], page_no, width, height)
            row_items.append({"text": text, "box": box, "tokens": tuple(toks)})

        if row_items:
            row_box = _union_box([p["box"] for p in row_items], page_no, width, height)
            row_text = " ".join(p["text"] for p in row_items)
            if len(row_items) == 1 and _PAGE_NUMBER_ONLY_RE.match(row_text):
                continue
            phrase_rows.append({"phrases": row_items, "box": row_box, "mid_y": line["mid_y"]})

    return phrase_rows, median_line_h, median_char_w


def _segment_blocks(phrase_rows: list[dict], median_line_h: float) -> list[list[dict]]:
    if len(phrase_rows) < 2:
        return []
    row_gap_limit = max(36.0, median_line_h * 3.5)
    subheader_gap_limit = max(24.0, median_line_h * 2.5)

    blocks: list[list[dict]] = []
    i = 0
    while i < len(phrase_rows):
        row = phrase_rows[i]
        if len(row["phrases"]) < 2:
            i += 1
            continue
        block = [row]
        j = i + 1
        while j < len(phrase_rows):
            nxt = phrase_rows[j]
            vgap = nxt["box"].top - block[-1]["box"].bottom
            if vgap > row_gap_limit:
                break
            horiz_overlap = min(block[-1]["box"].right, nxt["box"].right) - max(
                block[-1]["box"].left, nxt["box"].left
            )
            if horiz_overlap < -40.0 and len(nxt["phrases"]) >= 2:
                break
            if len(nxt["phrases"]) >= 2:
                block.append(nxt)
                j += 1
                continue
            # Allow a single-phrase section subheader embedded strictly inside a multi-row table.
            if (
                j + 1 < len(phrase_rows)
                and len(phrase_rows[j + 1]["phrases"]) >= 2
                and vgap <= subheader_gap_limit
                and (phrase_rows[j + 1]["box"].top - nxt["box"].bottom) <= subheader_gap_limit
                and abs(nxt["phrases"][0]["box"].left - block[0]["box"].left) <= 24.0
                and len(nxt["phrases"][0]["text"]) <= 90
            ):
                block.append(nxt)
                j += 1
                continue
            break

        multi_rows = sum(1 for r in block if len(r["phrases"]) >= 2)
        if len(block) >= 2 and multi_rows >= 2:
            blocks.append(block)
            i = j
        else:
            i += 1
    return blocks


def _merge_intervals(intervals: list[tuple[float, float]], pad: float) -> list[tuple[float, float]]:
    if not intervals:
        return []
    merged: list[list[float]] = []
    for l, r in sorted(intervals):
        if merged and l - pad <= merged[-1][1] + pad:
            merged[-1][1] = max(merged[-1][1], r)
        else:
            merged.append([l, r])
    return [(a, b) for a, b in merged]


def _infer_column_bands(block: list[dict], char_w: float) -> list[tuple[float, float]]:
    block_left = min(r["box"].left for r in block)
    block_right = max(r["box"].right for r in block)
    block_width = max(1.0, block_right - block_left)
    pad = max(1.0, min(4.0, char_w * 0.35))

    multi_rows = [r for r in block if len(r["phrases"]) >= 2]
    if len(multi_rows) < 2:
        return []
    core_start = 1 if len(multi_rows) >= 3 else 0
    core_intervals: list[tuple[float, float]] = []
    for row in multi_rows[core_start:]:
        for phrase in row["phrases"]:
            pw = phrase["box"].right - phrase["box"].left
            if pw <= block_width * 0.7:
                core_intervals.append((phrase["box"].left, phrase["box"].right))

    bands = _merge_intervals(core_intervals, pad)
    if len(bands) < 2:
        all_intervals = [
            (p["box"].left, p["box"].right)
            for row in multi_rows
            for p in row["phrases"]
            if (p["box"].right - p["box"].left) <= block_width * 0.7
        ]
        bands = _merge_intervals(all_intervals, pad)

    # Fold non-spanning phrases from any top header rows without collapsing distinct bands.
    for row in multi_rows[:core_start]:
        for phrase in row["phrases"]:
            pl, pr = phrase["box"].left, phrase["box"].right
            overlaps = [
                idx
                for idx, (bl, br) in enumerate(bands)
                if min(pr + pad, br) - max(pl - pad, bl) > 0.0
            ]
            if len(overlaps) == 1:
                idx = overlaps[0]
                bands[idx] = (min(bands[idx][0], pl), max(bands[idx][1], pr))
            elif not overlaps:
                bands.append((pl, pr))
                bands.sort()
    return bands


def _is_body_amount(text: str) -> bool:
    stripped = text.strip(" \t\u00a0")
    if not stripped or _YEAR_RE.fullmatch(stripped):
        return False
    return infer_interpretation(stripped, "data") in (
        "en_us_number",
        "eu_number",
        "indian_number",
        "percent",
    )


def _build_table(
    block: list[dict],
    bands: list[tuple[float, float]],
    page_no: int,
    width: float,
    height: float,
    source_hash: str,
    table_idx: int,
    tokens: tuple[SourceToken, ...],
) -> tuple[ExtractedTable, bool] | None:
    ncols = len(bands)
    nrows = len(block)
    if nrows < 2 or not (2 <= ncols <= 60) or nrows > 500 or nrows * ncols > 10_000:
        return None

    table_id = str(uuid5(NAMESPACE_URL, f"native_fast:{source_hash}:{page_no}:{table_idx}"))
    table_box = _union_box([r["box"] for r in block], page_no, width, height)
    table_ref = SourceReference(page=page_no, precision="table", box=table_box)

    # Assign each phrase to overlapping column band(s).
    slot_phrases: dict[tuple[int, int], list[dict]] = {}
    span_map: dict[tuple[int, int], int] = {}
    covered: set[tuple[int, int]] = set()

    for r_idx, row in enumerate(block):
        if len(row["phrases"]) == 1 and (row["box"].right - row["box"].left) > (
            bands[-1][1] - bands[0][0]
        ) * 0.55:
            slot_phrases[(r_idx, 0)] = [row["phrases"][0]]
            span_map[(r_idx, 0)] = ncols
            for c_idx in range(1, ncols):
                covered.add((r_idx, c_idx))
            continue

        for phrase in sorted(row["phrases"], key=lambda p: p["box"].left):
            pl, pr = phrase["box"].left, phrase["box"].right
            matching = [
                idx
                for idx, (bl, br) in enumerate(bands)
                if min(pr, br) - max(pl, bl) > 0.5
            ]
            if not matching:
                cx = (pl + pr) / 2.0
                nearest = min(
                    range(ncols),
                    key=lambda idx: abs(((bands[idx][0] + bands[idx][1]) / 2.0) - cx),
                )
                matching = [nearest]
            start_col = matching[0]
            contiguous_end = start_col
            for idx in matching[1:]:
                if idx == contiguous_end + 1:
                    contiguous_end = idx
                else:
                    break
            col_span = contiguous_end - start_col + 1
            if (r_idx, start_col) in covered:
                # Fall back to single un-covered nearest band.
                start_col = min(matching)
                col_span = 1
            slot_phrases.setdefault((r_idx, start_col), []).append(phrase)
            if col_span > 1 and all((r_idx, c) not in slot_phrases for c in range(start_col + 1, start_col + col_span)):
                span_map[(r_idx, start_col)] = max(span_map.get((r_idx, start_col), 1), col_span)
                for c in range(start_col + 1, start_col + col_span):
                    covered.add((r_idx, c))

    # Determine header rows from top up to the first row containing a real financial/numeric amount.
    header_end = 0
    for r_idx in range(min(3, nrows - 1)):
        row_texts = [
            " ".join(p["text"] for p in slot_phrases.get((r_idx, c), ()))
            for c in range(ncols)
        ]
        if any(_is_body_amount(t) for t in row_texts):
            break
        if any(t.strip() for t in row_texts):
            header_end = r_idx + 1
    if header_end == 0:
        row0_texts = [
            " ".join(p["text"] for p in slot_phrases.get((0, c), ()))
            for c in range(ncols)
        ]
        if any(any(ch.isalpha() for ch in t) for t in row0_texts) and not any(
            _is_body_amount(t) for t in row0_texts
        ):
            header_end = 1

    # Detect whether col 0 is predominantly text labels in body rows.
    col0_body_texts = [
        " ".join(p["text"] for p in slot_phrases.get((r_idx, 0), ())).strip()
        for r_idx in range(header_end, nrows)
    ]
    col0_nonempty = [t for t in col0_body_texts if t]
    col0_is_labels = bool(
        col0_nonempty
        and sum(1 for t in col0_nonempty if infer_interpretation(t, "data") == "text")
        >= len(col0_nonempty) * 0.6
    )

    cells: list[CellContent] = []
    for r_idx in range(nrows):
        is_header = r_idx < header_end
        is_section_row = span_map.get((r_idx, 0), 1) == ncols and not is_header
        for c_idx in range(ncols):
            if (r_idx, c_idx) in covered:
                continue
            c_span = span_map.get((r_idx, c_idx), 1)
            grouped = slot_phrases.get((r_idx, c_idx), [])
            text = " ".join(p["text"] for p in grouped).strip()
            phrase_tokens = tuple(tok for p in grouped for tok in p["tokens"])
            if is_header:
                role = "column_header"
            elif is_section_row:
                role = "section"
            elif c_idx == 0 and col0_is_labels and text:
                role = "row_header"
            else:
                role = "data"
            interpretation = infer_interpretation(text, role) if text else "uncertain"
            if phrase_tokens:
                cell_box = _union_box([t.box for t in phrase_tokens], page_no, width, height)
                ref = SourceReference(page=page_no, precision="cell", box=cell_box)
                refs = tuple(tok.provenance for tok in phrase_tokens)
            else:
                ref = table_ref
                refs = ()
            cells.append(
                CellContent(
                    id=str(uuid5(NAMESPACE_URL, f"{table_id}:{r_idx}:{c_idx}")),
                    row=r_idx,
                    col=c_idx,
                    col_span=c_span,
                    raw_text=text,
                    text=text,
                    role=role,
                    interpretation=interpretation,
                    reference=ref,
                    source_references=refs,
                )
            )

    cell_tuple = tuple(cells)
    header_rows, row_label_cols = infer_header_roles(cell_tuple)
    if col0_is_labels and 0 not in row_label_cols:
        row_label_cols = (0,)
    content = TableContent(
        rows=nrows,
        columns=ncols,
        cells=cell_tuple,
        header_rows=header_rows,
        row_label_columns=row_label_cols,
        source_reference=table_ref,
    )
    content, _ = infer_aligned_row_labels(content)
    content, _ = normalize_header_stubs(content)
    validate_grid(content)

    table_tokens = tuple(
        t
        for t in tokens
        if t.box.left < table_box.right
        and t.box.right > table_box.left
        and t.box.top < table_box.bottom
        and t.box.bottom > table_box.top
    )
    confident = _assess_table_confidence(content, header_end)
    return (
        ExtractedTable(
            id=table_id,
            page_no=page_no,
            region=table_box,
            content=content,
            source_tokens=table_tokens,
        ),
        confident,
    )


def _assess_table_confidence(content: TableContent, header_end: int) -> bool:
    if content.rows < 3 or content.columns < 2:
        return False
    body_rows = range(header_end, content.rows)
    if not body_rows:
        return False
    numeric_kinds = {"en_us_number", "eu_number", "indian_number", "percent", "dash_zero"}
    has_numeric_col = False
    for col in range(content.columns):
        col_cells = [c for c in content.cells if c.col == col and c.row in body_rows and c.text.strip()]
        if col_cells and sum(c.interpretation in numeric_kinds for c in col_cells) >= max(
            2, len(col_cells) * 0.5
        ):
            has_numeric_col = True
            break
    if not has_numeric_col:
        return False
    populated_body_rows = sum(
        1
        for r in body_rows
        if sum(1 for c in content.cells if c.row == r and c.text.strip()) >= 2
    )
    return populated_body_rows >= max(2, len(body_rows) * 0.6)


class NativeFastExtractor:
    """Zero-ML fast extractor for clean born-digital financial PDFs."""

    name = "native_fast"

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        timing = TimingLedger()
        info = timing.call("preflight", inspect_pdf, request.source_path)
        if info.encrypted:
            raise ValueError("Encrypted PDFs are unsupported")
        if max(request.pages) > info.page_count:
            raise ValueError("Selected page is outside this PDF")

        source_hash = timing.call("source_identity", _sha256_file, request.source_path)
        pages = timing.call(
            "page_metadata",
            lambda: tuple(page_info(request.source_path, p) for p in request.pages),
        )
        if request.region is not None:
            target = pages[0]
            if (
                abs(request.region.page_width - target.width) > 0.01
                or abs(request.region.page_height - target.height) > 0.01
            ):
                raise ValueError("Region dimensions do not match the source page")

        started = time.monotonic()
        process = psutil.Process()
        peak_rss = process.memory_info().rss

        tables: list[ExtractedTable] = []
        warnings: list[str] = []
        outcomes: list[dict] = []

        for page in pages:
            page_started = time.monotonic()
            if page.requires_ocr or not page.digital_text_present:
                warnings.append(
                    f"Page {page.page_no}: raster/OCR page requires ML extraction backend"
                )
                outcomes.append(
                    {
                        "original_page": page.page_no,
                        "backend_page": page.page_no,
                        "input_sha256": source_hash,
                        "status": "failure",
                        "errors": [],
                        "adapter_status": "skipped",
                        "table_count": 0,
                        "confident": False,
                        "elapsed_seconds": time.monotonic() - page_started,
                    }
                )
                continue

            tokens = timing.call(
                "source_tokens",
                source_tokens,
                request.source_path,
                page.page_no,
                source_hash=source_hash,
            )
            scoped = _filter_tokens_for_region(tokens, request.region)
            phrase_rows, median_line_h, median_char_w = _group_phrases(
                scoped, page.width, page.height, page.page_no
            )
            blocks = _segment_blocks(phrase_rows, median_line_h)

            page_tables: list[ExtractedTable] = []
            block_confidences: list[bool] = []
            for block in blocks:
                bands = _infer_column_bands(block, median_char_w)
                if not bands:
                    continue
                built = _build_table(
                    block,
                    bands,
                    page.page_no,
                    page.width,
                    page.height,
                    source_hash,
                    len(page_tables),
                    tokens,
                )
                if built is None:
                    continue
                extracted, block_confident = built
                page_tables.append(extracted)
                block_confidences.append(block_confident)

            # Attach nearby page caption/unit evidence excluding other extracted tables.
            enriched: list[ExtractedTable] = []
            for table in page_tables:
                sources = caption_sources(
                    table.region,
                    tokens,
                    excluded_regions=tuple(
                        other.region for other in page_tables if other.id != table.id
                    ),
                )
                if sources:
                    table = table.model_copy(
                        update={"content": table.content.model_copy(update={"context_sources": sources})}
                    )
                enriched.append(table)
            page_tables = enriched
            page_confident = bool(page_tables) and all(block_confidences)

            if page.mixed_image_content and request.region is None:
                warnings.append(
                    f"Page {page.page_no}: PDF text and images share this page; "
                    "select an image region or use Docling if an image table is missing"
                )
                page_confident = False

            tables.extend(page_tables)
            peak_rss = max(peak_rss, process.memory_info().rss)
            outcomes.append(
                {
                    "original_page": page.page_no,
                    "backend_page": page.page_no,
                    "input_sha256": source_hash,
                    "status": "success",
                    "errors": [],
                    "adapter_status": "success",
                    "table_count": len(page_tables),
                    "confident": page_confident,
                    "elapsed_seconds": time.monotonic() - page_started,
                }
            )

        clean = all(item["status"] == "success" for item in outcomes)
        usable = any(item["status"] in {"success", "partial_success"} for item in outcomes)
        return ExtractionResult(
            tables=tuple(tables),
            page_info=pages,
            warnings=tuple(warnings),
            manifest={
                "backend": self.name,
                "source_sha256": source_hash,
                "device": "cpu",
                "config": request.config.model_dump(mode="json"),
                "conversion_outcome": "success" if clean else "partial_success" if usable else "failure",
                "page_outcomes": outcomes,
                "elapsed_seconds": time.monotonic() - started,
                "peak_rss_bytes": peak_rss,
                "pages": request.pages,
                "ocr": False,
                "ocr_pages": [],
                "remote_services": False,
                "confident": bool(outcomes) and all(item.get("confident", False) for item in outcomes),
                "timing": timing.snapshot(),
            },
        )
