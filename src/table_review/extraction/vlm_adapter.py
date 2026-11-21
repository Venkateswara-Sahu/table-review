"""Opt-in Vision-Language Model extraction backend using OpenAI-compatible chat completions."""

import base64
import hashlib
import io
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
import psutil
from PIL import Image
from pydantic import Field, model_validator

from table_review.documents.pdf import inspect_pdf, page_info, render_page, source_tokens
from table_review.documents.types import ImmutableModel, PageBox, SourceReference
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

_PROMPT = (
    "Extract all financial or structured data tables visible in this document image. "
    "Return strictly valid JSON matching this schema with no commentary: "
    '{"tables": [{"title": string or null, "units": string or null, '
    '"header_row_count": integer >= 0, "rows": [[string, ...], ...]}]}. '
    "Preserve exact cell strings including currency symbols, parentheses, commas, decimals, "
    "dashes, and percent signs."
)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    lower = hostname.lower()
    return lower in {"localhost", "127.0.0.1", "::1", "[::1]"} or lower.startswith("127.")


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


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


def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    match = _CODE_FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text


def _filter_tokens_for_region(
    tokens: tuple[SourceToken, ...], region: PageBox | None
) -> tuple[SourceToken, ...]:
    if region is None:
        return tokens
    return tuple(
        tok
        for tok in tokens
        if region.left - 1.0 <= (tok.box.left + tok.box.right) / 2.0 <= region.right + 1.0
        and region.top - 1.0 <= (tok.box.top + tok.box.bottom) / 2.0 <= region.bottom + 1.0
    )


def _ground_cell(
    cell_text: str,
    tokens: tuple[SourceToken, ...],
    cursor: int,
    page_no: int,
    width: float,
    height: float,
) -> tuple[tuple[SourceToken, ...], PageBox | None, int]:
    target = _normalize_ws(cell_text)
    if not target or not tokens:
        return (), None, cursor

    n = len(tokens)
    search_order = list(range(cursor, n)) + list(range(cursor))
    for start in search_order:
        acc_space = ""
        acc_tight = ""
        end = start
        while end < n and end - start < 24:
            piece = _normalize_ws(tokens[end].text)
            acc_space = piece if not acc_space else f"{acc_space} {piece}"
            acc_tight = f"{acc_tight}{piece}"
            if acc_space == target or acc_tight == target:
                matched = tokens[start : end + 1]
                box = _union_box([t.box for t in matched], page_no, width, height)
                return matched, box, (end + 1) % max(1, n)
            if not target.startswith(acc_space) and not target.startswith(acc_tight):
                break
            end += 1
    return (), None, cursor


class VlmConfig(ImmutableModel):
    base_url: str
    model: str
    api_key: str | None = Field(default=None, repr=False)
    timeout_seconds: float = Field(default=120.0, gt=0)
    deployment_mode: Literal["local", "hosted"] = "local"
    allow_vlm_in_hosted: bool = False

    @model_validator(mode="after")
    def validate_endpoint(self):
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or any(ch.isspace() for ch in self.base_url)
        ):
            raise ValueError("VLM base_url must use http or https with a valid hostname")
        if not self.model.strip():
            raise ValueError("VLM model name must not be empty")
        return self


class VlmExtractor:
    """Opt-in Vision-Language Model extractor compatible with OpenAI chat completions APIs."""

    name = "vlm"

    def __init__(self, config: VlmConfig, *, client: httpx.Client | None = None):
        self.config = config
        self._client = client

    def _encode_page_png(self, source_path: Path, page, region: PageBox | None) -> bytes:
        page_area = max(1.0, page.width * page.height)
        scale = min(2.0, math.sqrt(16_000_000 / page_area) * 0.999)
        rendered = render_page(source_path, page.page_no, scale)
        try:
            if region is None:
                return rendered.image_path.read_bytes()
            with Image.open(rendered.image_path) as image:
                pw, ph = image.size
                left = max(0, min(pw - 1, round(region.left * scale)))
                top = max(0, min(ph - 1, round(region.top * scale)))
                right = max(left + 1, min(pw, round(region.right * scale)))
                bottom = max(top + 1, min(ph, round(region.bottom * scale)))
                cropped = image.crop((left, top, right, bottom))
                buf = io.BytesIO()
                cropped.save(buf, format="PNG")
                return buf.getvalue()
        finally:
            rendered.image_path.unlink(missing_ok=True)

    def _call_vlm(self, png_bytes: bytes) -> dict[str, Any]:
        encoded = base64.b64encode(png_bytes).decode("ascii")
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "temperature": 0.0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }
            ],
        }
        if self._client is not None:
            response = self._client.post(url, json=payload, headers=headers)
        else:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("VLM response contained no choices")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            raise TypeError("VLM returned unsupported content structure")
        parsed = json.loads(_strip_json_fences(content))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("tables"), list):
            raise TypeError("VLM JSON must contain a top-level 'tables' list")
        return parsed

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        if self.config.deployment_mode == "hosted" and not self.config.allow_vlm_in_hosted:
            raise ValueError("VLM extraction is disabled in strict offline hosted mode")

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
        parsed_url = urlsplit(self.config.base_url)

        tables: list[ExtractedTable] = []
        warnings: list[str] = []
        outcomes: list[dict] = []
        ocr_pages: list[int] = []

        for page in pages:
            page_started = time.monotonic()
            tokens: tuple[SourceToken, ...] = ()
            if page.digital_text_present:
                tokens = timing.call(
                    "source_tokens",
                    source_tokens,
                    request.source_path,
                    page.page_no,
                    source_hash=source_hash,
                )
            scoped_tokens = _filter_tokens_for_region(tokens, request.region)
            if not page.digital_text_present:
                ocr_pages.append(page.page_no)

            try:
                png_bytes = timing.call(
                    "page_render", self._encode_page_png, request.source_path, page, request.region
                )
                vlm_payload = timing.call("vlm_inference", self._call_vlm, png_bytes)
                page_tables = _adapt_vlm_tables(
                    vlm_payload["tables"],
                    page,
                    source_hash,
                    scoped_tokens,
                    tokens,
                    request.region,
                )
                tables.extend(page_tables)
                status = "success"
                adapter_status = "success"
                if not page.digital_text_present:
                    warnings.append(
                        f"Page {page.page_no}: VLM extracted without digital text grounding; verify cells against the image"
                    )
            except (MemoryError, OSError):
                raise
            except Exception as error:  # noqa: BLE001 - isolate page-level VLM/parse failure
                page_tables = []
                status = "failure"
                adapter_status = "failure"
                warnings.append(
                    f"Page {page.page_no}: VLM extraction failed ({type(error).__name__})"
                )

            peak_rss = max(peak_rss, process.memory_info().rss)
            outcomes.append(
                {
                    "original_page": page.page_no,
                    "backend_page": page.page_no,
                    "input_sha256": source_hash,
                    "status": status,
                    "errors": [],
                    "adapter_status": adapter_status,
                    "table_count": len(page_tables),
                    "elapsed_seconds": time.monotonic() - page_started,
                }
            )

        clean = all(item["status"] == "success" for item in outcomes)
        usable = any(item["status"] == "success" for item in outcomes)
        return ExtractionResult(
            tables=tuple(tables),
            page_info=pages,
            warnings=tuple(warnings),
            manifest={
                "backend": self.name,
                "vlm_model": self.config.model,
                "vlm_endpoint_host": parsed_url.hostname,
                "source_sha256": source_hash,
                "device": "remote",
                "config": request.config.model_dump(mode="json"),
                "conversion_outcome": "success" if clean else "partial_success" if usable else "failure",
                "page_outcomes": outcomes,
                "elapsed_seconds": time.monotonic() - started,
                "peak_rss_bytes": peak_rss,
                "pages": request.pages,
                "ocr": bool(ocr_pages),
                "ocr_pages": ocr_pages,
                "remote_services": not _is_loopback(parsed_url.hostname),
                "timing": timing.snapshot(),
            },
        )


def _adapt_vlm_tables(
    raw_tables: list[Any],
    page,
    source_hash: str,
    scoped_tokens: tuple[SourceToken, ...],
    all_tokens: tuple[SourceToken, ...],
    request_region: PageBox | None,
) -> list[ExtractedTable]:
    adapted: list[ExtractedTable] = []
    token_cursor = 0

    for idx, item in enumerate(raw_tables):
        if not isinstance(item, dict):
            raise TypeError("Each VLM table entry must be an object")
        raw_rows = item.get("rows")
        if not isinstance(raw_rows, list) or not raw_rows:
            continue
        norm_rows = [[("" if val is None else str(val)) for val in row] for row in raw_rows if isinstance(row, list)]
        if not norm_rows:
            continue
        ncols = max(len(r) for r in norm_rows)
        nrows = len(norm_rows)
        if ncols < 1 or nrows > 500 or ncols > 60 or nrows * ncols > 10_000:
            raise ValueError("VLM table dimensions out of supported range")

        padded_rows = [row + [""] * (ncols - len(row)) for row in norm_rows]
        header_count = int(item.get("header_row_count", 1) or 0)
        header_count = max(0, min(nrows - 1 if nrows > 1 else nrows, header_count))

        col0_nonempty = [padded_rows[r][0].strip() for r in range(header_count, nrows) if padded_rows[r][0].strip()]
        col0_is_labels = bool(
            col0_nonempty
            and ncols >= 2
            and sum(1 for t in col0_nonempty if infer_interpretation(t, "data") == "text")
            >= len(col0_nonempty) * 0.6
        )

        table_id = str(uuid5(NAMESPACE_URL, f"vlm:{source_hash}:{page.page_no}:{idx}"))
        fallback_ref = SourceReference(
            page=page.page_no,
            precision="table" if request_region else "page",
            box=request_region,
        )

        matched_tokens_all: list[SourceToken] = []
        matched_boxes: list[PageBox] = []
        raw_cells: list[tuple[int, int, str, str, str, tuple[SourceToken, ...], PageBox | None]] = []

        for r_idx in range(nrows):
            is_header = r_idx < header_count
            for c_idx in range(ncols):
                text = padded_rows[r_idx][c_idx].strip()
                if is_header:
                    role = "column_header"
                elif c_idx == 0 and col0_is_labels and text:
                    role = "row_header"
                else:
                    role = "data"
                interp = infer_interpretation(text, role) if text else "uncertain"
                matched_toks, cell_box, token_cursor = _ground_cell(
                    text, scoped_tokens, token_cursor, page.page_no, page.width, page.height
                )
                if matched_toks:
                    matched_tokens_all.extend(matched_toks)
                if cell_box is not None:
                    matched_boxes.append(cell_box)
                raw_cells.append((r_idx, c_idx, text, role, interp, matched_toks, cell_box))

        table_region = (
            _union_box(matched_boxes, page.page_no, page.width, page.height)
            if matched_boxes
            else request_region
        )
        table_ref = SourceReference(
            page=page.page_no,
            precision="table" if table_region else "page",
            box=table_region,
        )
        cells = []
        for r_idx, c_idx, text, role, interp, matched_toks, cell_box in raw_cells:
            if cell_box is not None:
                ref = SourceReference(page=page.page_no, precision="cell", box=cell_box)
                refs = tuple(tok.provenance for tok in matched_toks)
            else:
                ref = table_ref if table_region else fallback_ref
                refs = ()
            cells.append(
                CellContent(
                    id=str(uuid5(NAMESPACE_URL, f"{table_id}:{r_idx}:{c_idx}")),
                    row=r_idx,
                    col=c_idx,
                    raw_text=text,
                    text=text,
                    role=role,
                    interpretation=interp,
                    reference=ref,
                    source_references=refs,
                )
            )

        cell_tuple = tuple(cells)
        header_rows, row_label_cols = infer_header_roles(cell_tuple)
        if col0_is_labels and 0 not in row_label_cols:
            row_label_cols = (0,)
        raw_title = item.get("title")
        clean_title = " ".join(str(raw_title).split())[:200] if isinstance(raw_title, str) and raw_title.strip() else None
        raw_units = item.get("units")
        unit_notes = (" ".join(str(raw_units).split())[:200],) if isinstance(raw_units, str) and raw_units.strip() else ()

        content = TableContent(
            rows=nrows,
            columns=ncols,
            cells=cell_tuple,
            header_rows=header_rows,
            row_label_columns=row_label_cols,
            unit_notes=unit_notes,
            title=clean_title,
            source_reference=table_ref,
        )
        content, _ = infer_aligned_row_labels(content)
        content, _ = normalize_header_stubs(content)
        validate_grid(content)

        adapted.append(
            ExtractedTable(
                id=table_id,
                page_no=page.page_no,
                region=table_region,
                content=content,
                source_tokens=tuple({t.id: t for t in matched_tokens_all}.values()),
            )
        )

    final_tables: list[ExtractedTable] = []
    for table in adapted:
        if table.region and all_tokens:
            sources = caption_sources(
                table.region,
                all_tokens,
                excluded_regions=tuple(other.region for other in adapted if other.id != table.id),
            )
            if sources:
                table = table.model_copy(
                    update={"content": table.content.model_copy(update={"context_sources": sources})}
                )
        final_tables.append(table)
    return final_tables
