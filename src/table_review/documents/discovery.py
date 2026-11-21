"""Fast, ML-free PDF discovery: document outline/TOC, text search with boxes, and table page scanning."""

import re
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
from pydantic import Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from table_review.documents.pdf_native import map_charbox, serialized_pdfium, visible_char
from table_review.documents.pdf_native import page as _page
from table_review.documents.types import ImmutableModel, PageBox

_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    re.IGNORECASE,
)
_PAGE_FOOTER_RE = re.compile(r"^\s*(?:page\s+)?\d+\s*(?:of\s*\d+)?\s*$", re.IGNORECASE)
_NUM_TOKEN_RE = re.compile(
    r"(?:[$€£₹¥]\s*)?\(?[-−+]?\d[\d,.]*(?:\.\d+)?%?\)?(?!\w)"
)
_YEAR_OR_PERIOD_RE = re.compile(r"\b(?:(?:19|20)\d{2}|FY\s*\d{2,4}|Q[1-4])\b", re.IGNORECASE)

_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("balance_sheet", re.compile(r"\bbalance\s+sheets?\b", re.IGNORECASE)),
    (
        "income_statement",
        re.compile(
            r"\bstatements?\s+of\s+(?:operations|income|earnings|loss|comprehensive|profit)\b",
            re.IGNORECASE,
        ),
    ),
    ("cash_flows", re.compile(r"\bcash\s+flows?\b", re.IGNORECASE)),
    (
        "equity",
        re.compile(
            r"\b(?:stockholders'|shareholders'|changes\s+in)\s+(?:equity|deficit)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "unit_scale",
        re.compile(r"\b(?:in\s+)?(?:thousands|millions|billions|crores|lakhs)\b", re.IGNORECASE),
    ),
    ("totals", re.compile(r"\b(?:total|subtotal)\b", re.IGNORECASE)),
    (
        "currency",
        re.compile(r"[$€£₹¥]|\b(?:USD|EUR|GBP|INR|JPY|CAD|AUD|CHF|CNY)\b"),
    ),
)


class OutlineItem(ImmutableModel):
    title: str
    page_no: int | None = None
    depth: int = Field(ge=0)
    children: tuple["OutlineItem", ...] = ()


OutlineItem.model_rebuild()


class DocumentOutline(ImmutableModel):
    items: tuple[OutlineItem, ...] = ()
    total_count: int = 0


class SearchMatch(ImmutableModel):
    page_no: int = Field(ge=1)
    snippet: str
    match_start: int = Field(ge=0)
    match_end: int = Field(ge=0)
    boxes: tuple[PageBox, ...] = ()


class DocumentSearchResult(ImmutableModel):
    query: str
    total_matches: int = 0
    truncated: bool = False
    matches: tuple[SearchMatch, ...] = ()


class TableCandidate(ImmutableModel):
    page_no: int = Field(ge=1)
    score: float = Field(ge=0.0, le=1.0)
    numeric_lines: int = Field(ge=0)
    numeric_tokens: int = Field(ge=0)
    heading: str | None = None
    signals: tuple[str, ...] = ()


class TableCandidatesResult(ImmutableModel):
    page_count: int = Field(ge=0)
    candidates: tuple[TableCandidate, ...] = ()


def extract_outline(path: Path, *, max_items: int = 500, max_depth: int = 12) -> DocumentOutline:
    try:
        reader = PdfReader(path)
        raw_outline = reader.outline
    except (PdfReadError, OSError, ValueError):
        return DocumentOutline()
    if not raw_outline:
        return DocumentOutline()

    count = [0]

    def resolve_page(entry) -> int | None:
        try:
            index = reader.get_destination_page_number(entry)
            if isinstance(index, int) and index >= 0:
                return index + 1
        except Exception:  # noqa: BLE001 - malformed PDF outline destinations vary widely
            return None
        return None

    def extract_title(entry) -> str:
        raw = getattr(entry, "title", None)
        if raw is None and isinstance(entry, dict):
            raw = entry.get("/Title")
        cleaned = " ".join(str(raw or "").split())
        return cleaned[:200] if cleaned else "Untitled"

    def parse_level(entries, depth: int) -> list[dict]:
        nodes: list[dict] = []
        for entry in entries:
            if count[0] >= max_items:
                break
            if isinstance(entry, list):
                if depth + 1 >= max_depth:
                    continue
                children = parse_level(entry, depth + 1)
                if nodes:
                    nodes[-1]["children"].extend(children)
                else:
                    nodes.extend(children)
            else:
                count[0] += 1
                nodes.append(
                    {
                        "title": extract_title(entry),
                        "page_no": resolve_page(entry),
                        "depth": depth,
                        "children": [],
                    }
                )
        return nodes

    def freeze(node: dict) -> OutlineItem:
        return OutlineItem(
            title=node["title"],
            page_no=node["page_no"],
            depth=node["depth"],
            children=tuple(freeze(child) for child in node["children"]),
        )

    parsed = parse_level(raw_outline, 0)
    return DocumentOutline(items=tuple(freeze(node) for node in parsed), total_count=count[0])


def _merge_line_boxes(
    rects: list[tuple[float, float, float, float]], page_no: int, width: float, height: float
) -> tuple[PageBox, ...]:
    if not rects:
        return ()
    groups: list[list[tuple[float, float, float, float]]] = []
    for rect in sorted(rects, key=lambda r: (r[1], r[0])):
        l, t, r, b = rect
        l = max(0.0, min(l, width))
        r = max(0.0, min(r, width))
        t = max(0.0, min(t, height))
        b = max(0.0, min(b, height))
        if l >= r or t >= b:
            continue
        if groups and abs(groups[-1][0][1] - t) <= 6.0:
            groups[-1].append((l, t, r, b))
        else:
            groups.append([(l, t, r, b)])
    boxes = []
    for group in groups:
        boxes.append(
            PageBox(
                page=page_no,
                left=min(g[0] for g in group),
                top=min(g[1] for g in group),
                right=max(g[2] for g in group),
                bottom=max(g[3] for g in group),
                page_width=width,
                page_height=height,
            )
        )
    return tuple(boxes)


@serialized_pdfium
def search_document(
    path: Path, query: str, *, limit: int = 100, case_sensitive: bool = False
) -> DocumentSearchResult:
    needle = query.strip()
    if not needle:
        return DocumentSearchResult(query=query, total_matches=0)
    parts = needle.split()
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(r"\s+".join(re.escape(part) for part in parts), flags)

    matches: list[SearchMatch] = []
    total_matches = 0
    with pdfium.PdfDocument(path) as document:
        for page_no in range(1, len(document) + 1):
            with closing(_page(document, page_no)) as page, closing(
                page.get_textpage()
            ) as textpage:
                bounded = textpage.get_text_bounded()
                if not bounded or not pattern.search(bounded):
                    continue
                n_chars = textpage.count_chars()
                if n_chars <= 0:
                    continue
                raw_range = textpage.get_text_range(0, n_chars)
                if len(raw_range) == n_chars:
                    full_text = raw_range
                    idx_map = None
                else:
                    pieces = []
                    idx_map = []
                    for i in range(n_chars):
                        piece = textpage.get_text_range(i, 1) or ""
                        pieces.append(piece)
                        idx_map.extend([i] * len(piece))
                    full_text = "".join(pieces)

                width, height = page.get_size()
                crop_l, crop_b, crop_r, crop_t = page.get_bbox()
                unrotated_w, unrotated_h = crop_r - crop_l, crop_t - crop_b
                rotation = page.get_rotation()

                for found in pattern.finditer(full_text):
                    total_matches += 1
                    if len(matches) >= limit:
                        continue
                    start, end = found.start(), found.end()
                    raw_prefix = full_text[max(0, start - 45) : start]
                    raw_suffix = full_text[end : min(len(full_text), end + 45)]
                    prefix = re.sub(r"\s+", " ", raw_prefix).lstrip()
                    hit = re.sub(r"\s+", " ", found.group(0))
                    suffix = re.sub(r"\s+", " ", raw_suffix).rstrip()
                    snippet = prefix + hit + suffix

                    rects = []
                    seen_indices: set[int] = set()
                    for pos in range(start, end):
                        char_idx = idx_map[pos] if idx_map is not None else pos
                        if char_idx in seen_indices or not (0 <= char_idx < n_chars):
                            continue
                        seen_indices.add(char_idx)
                        if not visible_char(textpage, char_idx):
                            continue
                        ch = textpage.get_text_range(char_idx, 1)
                        if not ch or ch.isspace():
                            continue
                        l, b, r, t = textpage.get_charbox(char_idx)
                        rects.append(
                            map_charbox(
                                l, b, r, t, crop_l, crop_b, unrotated_w, unrotated_h, rotation
                            )
                        )
                    boxes = _merge_line_boxes(rects, page_no, width, height)
                    matches.append(
                        SearchMatch(
                            page_no=page_no,
                            snippet=snippet,
                            match_start=len(prefix),
                            match_end=len(prefix) + len(hit),
                            boxes=boxes,
                        )
                    )
    return DocumentSearchResult(
        query=query,
        total_matches=total_matches,
        truncated=total_matches > len(matches),
        matches=tuple(matches),
    )


def _is_readable_heading(clean: str) -> bool:
    if "\ufffd" in clean:
        return False
    tokens = clean.split()
    if not tokens:
        return False
    single_char = sum(1 for tok in tokens if len(tok) == 1)
    if len(tokens) >= 4 and single_char / len(tokens) > 0.5:
        return False
    words = re.findall(r"[A-Za-z]{3,}", clean)
    if not words:
        return False
    # Reject unmapped CID-font gibberish (e.g. "INIKNJJN+...") lacking normal vowels or packed with symbols.
    readable_words = sum(1 for w in words if re.search(r"[AEIOUYaeiouy]", w))
    if readable_words == 0:
        return False
    weird_symbols = sum(1 for c in clean if c in "+^~`|\\<>{}[]@#$%*=")
    return weird_symbols <= max(2, len(clean) * 0.08)


def _pick_heading(lines: list[str]) -> str | None:
    best_financial = None
    first_heading = None
    for raw in lines[:12]:
        clean = " ".join(raw.split())
        if not 6 <= len(clean) <= 120:
            continue
        if _PAGE_FOOTER_RE.match(clean):
            continue
        letters = sum(c.isalpha() for c in clean)
        if letters < max(4, len(clean) * 0.45):
            continue
        if not _is_readable_heading(clean):
            continue
        if any(
            pat.search(clean)
            for name, pat in _SIGNAL_PATTERNS
            if name in ("balance_sheet", "income_statement", "cash_flows", "equity")
        ):
            return clean
        if first_heading is None:
            first_heading = clean
        if best_financial is None and any(
            word in clean.lower()
            for word in ("table", "schedule", "statement", "assets", "revenue", "financial")
        ):
            best_financial = clean
    return best_financial or first_heading


@serialized_pdfium
def scan_table_candidates(path: Path, *, max_candidates: int = 80) -> TableCandidatesResult:
    candidates: list[TableCandidate] = []
    with pdfium.PdfDocument(path) as document:
        page_count = len(document)
        for page_no in range(1, page_count + 1):
            with closing(_page(document, page_no)) as page, closing(
                page.get_textpage()
            ) as textpage:
                bounded = textpage.get_text_bounded()
                if not bounded or not bounded.strip():
                    continue
                lines = [line.strip() for line in bounded.splitlines() if line.strip()]
                if not lines:
                    continue
                numeric_lines = 0
                numeric_tokens = 0
                for line in lines:
                    if _PAGE_FOOTER_RE.match(line):
                        continue
                    scrubbed = _DATE_RE.sub(" ", line)
                    tokens = _NUM_TOKEN_RE.findall(scrubbed)
                    if len(tokens) >= 2:
                        numeric_lines += 1
                        numeric_tokens += len(tokens)

                signals = [name for name, pat in _SIGNAL_PATTERNS if pat.search(bounded)]
                periods = {m.group(0).upper() for m in _YEAR_OR_PERIOD_RE.finditer(bounded)}
                if len(periods) >= 2:
                    signals.append("multi_period")

                qualifies = (numeric_lines >= 3 and numeric_tokens >= 6) or (
                    numeric_lines >= 2 and numeric_tokens >= 4 and len(signals) >= 2
                )
                if not qualifies:
                    continue

                score = min(
                    1.0,
                    round(
                        0.25 * min(1.0, numeric_lines / 12.0)
                        + 0.35 * min(1.0, numeric_tokens / 36.0)
                        + 0.40 * min(1.0, len(signals) / 4.0),
                        3,
                    ),
                )
                candidates.append(
                    TableCandidate(
                        page_no=page_no,
                        score=score,
                        numeric_lines=numeric_lines,
                        numeric_tokens=numeric_tokens,
                        heading=_pick_heading(lines),
                        signals=tuple(signals),
                    )
                )

    if len(candidates) > max_candidates:
        candidates.sort(key=lambda item: (-item.score, item.page_no))
        candidates = sorted(candidates[:max_candidates], key=lambda item: item.page_no)
    return TableCandidatesResult(page_count=page_count, candidates=tuple(candidates))
