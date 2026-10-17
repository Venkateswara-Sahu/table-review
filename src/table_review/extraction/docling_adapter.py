import hashlib
import math
import tempfile
import threading
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psutil
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

from table_review.documents.pdf import inspect_pdf, page_info, render_page, source_tokens
from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.context import caption_sources
from table_review.extraction.literals import reconcile_native_literals, reconcile_ocr_currency_rows
from table_review.extraction.marks import dash_candidates
from table_review.extraction.ocr import mark_reader, ocr_options, ocr_tokens
from table_review.extraction.provenance import conversion_outcome, runtime_manifest
from table_review.extraction.recovery import candidate_regions, intersects
from table_review.extraction.timing import TimingLedger
from table_review.extraction.types import (
    ExtractedTable,
    ExtractionRequest,
    ExtractionResult,
    SourceToken,
)
from table_review.tables.grid import validate_grid
from table_review.tables.types import CellContent, TableContent, infer_header_roles


def model_hashes(root: str):
    directory = Path(root)
    files = [
        file
        for file in directory.rglob("*")
        if file.is_file()
        and ".cache" not in file.relative_to(directory).parts
        and file.suffix in {".json", ".safetensors", ".onnx", ".pt", ".bin"}
    ]
    if not files:
        raise ValueError("Extraction models must be downloaded before starting the worker")
    hashes = {}
    for file in sorted(files):
        with file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        hashes[file.relative_to(directory).as_posix()] = digest
    return hashes


def _vector_page(reader, page, region, destination):
    """Clip in vector space after making rotation explicit; map back with translation."""
    source = reader.pages[page.page_no - 1]
    source.transfer_rotation_to_content()
    left, bottom, right, top = map(float, source.cropbox)
    if region:
        if (
            abs(region.page_width - page.width) > 0.01
            or abs(region.page_height - page.height) > 0.01
        ):
            raise ValueError("Region dimensions do not match the source page")
        left, right = left + region.left, left + region.right
        bottom, top = top - region.bottom, top - region.top
    width, height = right - left, top - bottom
    if width <= 0 or height <= 0:
        raise ValueError("Region must have a positive area")
    source.cropbox = RectangleObject((left, bottom, right, top))
    writer = PdfWriter()
    target = writer.add_blank_page(width=width, height=height)
    target.merge_transformed_page(source, Transformation().translate(-left, -bottom))
    writer.write(destination)
    return width, height, region.left if region else 0, region.top if region else 0


class DoclingExtractor:
    def __init__(self):
        self._converters = {}
        self._options = {}
        self._mark_readers = {}

    def _converter(self, config, use_ocr=False):
        key = config.model_dump_json() + f":ocr={use_ocr}"
        if key not in self._converters:
            from docling.datamodel.accelerator_options import (
                AcceleratorDevice,
                AcceleratorOptions,
            )
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.datamodel.settings import settings as docling_settings
            from docling.document_converter import DocumentConverter, PdfFormatOption

            from table_review.extraction.visible_backend import VisiblePdfBackend

            options = PdfPipelineOptions(
                artifacts_path=config.artifacts_path.resolve(),
                do_ocr=use_ocr,
                generate_parsed_pages=use_ocr,
                do_table_structure=True,
                do_code_enrichment=False,
                do_formula_enrichment=False,
                enable_remote_services=False,
                allow_external_plugins=False,
                accelerator_options=AcceleratorOptions(
                    device=AcceleratorDevice.CPU, num_threads=config.num_threads
                ),
            )
            if use_ocr:
                options.ocr_options = ocr_options(config)
            options.table_structure_options.mode = TableFormerMode.ACCURATE
            options.table_structure_options.do_cell_matching = config.cell_matching
            self._options[key] = options.model_dump(mode="json")
            docling_settings.debug.profile_pipeline_timings = True
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=options, backend=VisiblePdfBackend
                    ),
                }
            )
            if hasattr(self, "_timings"):
                self._timings.call(
                    "pipeline_initialization", converter.initialize_pipeline, InputFormat.PDF
                )
            else:
                converter.initialize_pipeline(InputFormat.PDF)
            self._converters[key] = converter
        return self._converters[key]

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        timing = self._timings = TimingLedger()
        info = timing.call("preflight", inspect_pdf, request.source_path)
        if info.encrypted:
            raise ValueError("Encrypted PDFs are unsupported")
        if max(request.pages) > info.page_count:
            raise ValueError("Selected page is outside this PDF")
        hashes = timing.call(
            "model_identity", model_hashes, str(request.config.artifacts_path.resolve())
        )
        source_hash = timing.call(
            "source_identity", lambda: hashlib.sha256(request.source_path.read_bytes()).hexdigest()
        )
        pages = timing.call(
            "page_metadata",
            lambda: tuple(page_info(request.source_path, page) for page in request.pages),
        )
        if request.region is None and any(
            not page.digital_text_present
            and not (page.requires_ocr and request.config.ocr_mode == "auto")
            for page in pages
        ):
            raise ValueError("Selected page has no usable digital text; OCR is unsupported")
        started = time.monotonic()
        process = psutil.Process()
        peak = [process.memory_info().rss]
        stop = threading.Event()

        def monitor():
            while not stop.wait(0.05):
                peak[0] = max(peak[0], process.memory_info().rss)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        warnings, tables, transforms, outcomes, ocr_pages = [], [], [], [], []
        used_options = {}
        try:
            reader = timing.call("pdf_reader", PdfReader, request.source_path)
            for page in pages:
                page_started = time.monotonic()
                use_ocr = page.requires_ocr and request.config.ocr_mode == "auto"
                tokens = timing.call(
                    "source_tokens", source_tokens, request.source_path, page.page_no
                )
                pdf_page = reader.pages[page.page_no - 1]
                ordinary = pdf_page.rotation == 0 and list(pdf_page.cropbox) == list(
                    pdf_page.mediabox
                )
                if ordinary and request.region is None:
                    backend_page = page.page_no
                    w, h, offset_left, offset_top = page.width, page.height, 0, 0
                    input_hash = source_hash
                    converted_path = request.source_path
                else:
                    backend_page = 1
                    with tempfile.TemporaryDirectory() as temporary:
                        normalized = Path(temporary) / "selected.pdf"
                        w, h, offset_left, offset_top = timing.call(
                            "normalization", _vector_page, reader, page, request.region, normalized
                        )
                        selected_info = page_info(normalized, 1)
                        use_ocr = selected_info.requires_ocr and request.config.ocr_mode == "auto"
                        if not selected_info.digital_text_present and not use_ocr:
                            raise ValueError(
                                "Selected region has no usable digital text or OCR image"
                            )
                        converter = timing.call(
                            "converter_setup", self._converter, request.config, use_ocr
                        )
                        input_hash = hashlib.sha256(normalized.read_bytes()).hexdigest()
                        result, outcome = timing.call(
                            "conversion",
                            self._convert,
                            converter,
                            normalized,
                            page.page_no,
                            backend_page,
                            input_hash,
                        )
                    transforms.append(
                        {
                            "original_page": page.page_no,
                            "backend_page": 1,
                            "offset_left": offset_left,
                            "offset_top": offset_top,
                            "width": w,
                            "height": h,
                            "kind": "vector_clip_after_rotation",
                        }
                    )
                if ordinary and request.region is None:
                    converter = timing.call(
                        "converter_setup", self._converter, request.config, use_ocr
                    )
                    result, outcome = timing.call(
                        "conversion",
                        self._convert,
                        converter,
                        converted_path,
                        page.page_no,
                        backend_page,
                        input_hash,
                    )
                outcomes.append(outcome)
                outcome["ocr"] = use_ocr
                option_key = request.config.model_dump_json() + f":ocr={use_ocr}"
                used_options["ocr" if use_ocr else "digital"] = self._options.get(option_key)
                if use_ocr:
                    ocr_pages.append(page.page_no)
                    warnings.append(
                        f"Page {page.page_no}: offline OCR used; verify every cell against the image"
                    )
                elif page.mixed_image_content and request.region is None:
                    warnings.append(
                        f"Page {page.page_no}: PDF text and images share this page; "
                        "if an image table is missing, select its region for offline OCR"
                    )
                outcome["elapsed_seconds"] = time.monotonic() - page_started
                if outcome["status"] != "success" or outcome["errors"]:
                    warnings.append(
                        f"Page {page.page_no}: conversion {outcome['status']}; inspect parser outcomes"
                    )
                if outcome["status"] not in {"success", "partial_success"}:
                    continue
                try:
                    if use_ocr:
                        tokens, token_warnings = timing.call(
                            "ocr_evidence",
                            ocr_tokens,
                            result,
                            page,
                            backend_page,
                            source_hash,
                            w,
                            h,
                            offset_left,
                            offset_top,
                        )
                        warnings.extend(token_warnings)
                    page_tables, page_warnings = timing.call(
                        "adaptation",
                        self._adapt_page,
                        result,
                        page,
                        backend_page,
                        w,
                        h,
                        offset_left,
                        offset_top,
                        source_hash,
                        tokens,
                        ocr=use_ocr,
                    )
                    if not use_ocr:
                        try:
                            reconciled, changes = [], []
                            for table in page_tables:
                                content, adjustments = reconcile_native_literals(
                                    table.content, tokens
                                )
                                reconciled.append(table.model_copy(update={"content": content}))
                                changes.extend(adjustments)
                            page_tables = reconciled
                            outcome["native_literal_reconciliation"] = {
                                "status": "success",
                                "policy": "precise_source_typography_and_complete_prefix_currency_rows_v1",
                                "changes": changes,
                                "parser_raw_text_preserved": True,
                            }
                        except (MemoryError, OSError):
                            raise
                        except Exception as error:  # noqa: BLE001 -- preserve primary tables on optional failure
                            outcome["native_literal_reconciliation"] = {
                                "status": "failure",
                                "exception_type": type(error).__name__,
                            }
                            page_warnings.append(
                                f"Page {page.page_no}: source literal reconciliation unavailable; primary tables retained"
                            )
                    if use_ocr and request.region is None and request.config.table_recovery:
                        recovered, recovery_warnings, attempts, recovery_transforms = timing.call(
                            "table_region_recovery",
                            self._recover,
                            request,
                            page,
                            converter,
                            tokens,
                            page_tables,
                            source_hash,
                        )
                        page_tables.extend(recovered)
                        page_warnings.extend(recovery_warnings)
                        outcome["recovery_attempts"] = attempts
                        transforms.extend(recovery_transforms)
                    if use_ocr and request.config.mark_recovery and page_tables:
                        try:
                            marked, mark_warning, mark_outcome = timing.call(
                                "mark_recovery",
                                self._recover_marks,
                                request,
                                page,
                                page_tables,
                                source_hash,
                            )
                            page_tables = marked
                            if mark_outcome.get("accepted"):
                                page_warnings = [
                                    warning
                                    for warning in page_warnings
                                    if not warning.startswith(f"Page {page.page_no}: OCR omitted")
                                ]
                                remaining = sum(
                                    not cell.text
                                    for table in page_tables
                                    for cell in table.content.cells
                                )
                                if remaining:
                                    page_warnings.append(
                                        f"Page {page.page_no}: OCR omitted {remaining} positions after recovery; review the editable blanks against the image"
                                    )
                        except (MemoryError, OSError):
                            raise
                        except Exception as error:  # noqa: BLE001 -- optional recognition preserves primary tables
                            mark_outcome = {
                                "status": "failure",
                                "exception_type": type(error).__name__,
                            }
                            mark_warning = [
                                f"Page {page.page_no}: optional mark recovery failed; primary tables retained"
                            ]
                        outcome["mark_recovery"] = mark_outcome
                        page_warnings.extend(mark_warning)
                    if use_ocr:
                        try:
                            reconciled, changes = [], []
                            for table in page_tables:
                                content, adjustments = reconcile_ocr_currency_rows(
                                    table.content, tokens
                                )
                                reconciled.append(table.model_copy(update={"content": content}))
                                changes.extend(adjustments)
                            page_tables = reconciled
                            outcome["ocr_currency_reconciliation"] = {
                                "status": "success",
                                "policy": "complete_prefix_sequence_coarse_evidence_v1",
                                "changes": changes,
                                "parser_raw_text_preserved": True,
                            }
                            if changes:
                                page_warnings.append(
                                    f"Page {page.page_no}: currency markers regrouped from OCR evidence; cell locations are coarse, verify values against the image"
                                )
                        except (MemoryError, OSError):
                            raise
                        except Exception as error:  # noqa: BLE001 -- optional reconciliation retains primary tables
                            outcome["ocr_currency_reconciliation"] = {
                                "status": "failure",
                                "exception_type": type(error).__name__,
                            }
                            page_warnings.append(
                                f"Page {page.page_no}: OCR currency reconciliation unavailable; primary tables retained"
                            )
                    try:
                        enriched = [
                            table.model_copy(
                                update={
                                    "content": table.content.model_copy(
                                        update={
                                            "context_sources": caption_sources(
                                                table.region,
                                                tokens,
                                                excluded_regions=tuple(
                                                    other.region
                                                    for other in page_tables
                                                    if other.id != table.id
                                                ),
                                            ),
                                        }
                                    )
                                }
                            )
                            for table in page_tables
                        ]
                        page_tables = enriched
                    except (MemoryError, OSError):
                        raise
                    except Exception:  # noqa: BLE001 -- optional evidence must retain valid tables
                        page_warnings.append(
                            f"Page {page.page_no}: caption context unavailable; primary tables retained"
                        )
                    tables.extend(page_tables)
                    warnings.extend(page_warnings)
                    outcome["adapter_status"] = "success"
                    outcome["table_count"] = len(page_tables)
                except (MemoryError, OSError):
                    raise
                except Exception as error:  # noqa: BLE001 -- reject a malformed page atomically
                    outcome["adapter_status"] = "failure"
                    outcome["adapter_exception_type"] = type(error).__name__
                    outcome["table_count"] = 0
                    warnings.append(
                        f"Page {page.page_no}: extracted grid rejected; inspect parser outcomes"
                    )
                outcome["elapsed_seconds"] = time.monotonic() - page_started
        finally:
            stop.set()
            thread.join()
            peak[0] = max(peak[0], process.memory_info().rss)
        clean = all(
            item["status"] == "success"
            and not item["errors"]
            and item.get("adapter_status") == "success"
            and item.get("mark_recovery", {}).get("status") != "failure"
            and item.get("native_literal_reconciliation", {}).get("status") != "failure"
            and item.get("ocr_currency_reconciliation", {}).get("status") != "failure"
            and all(
                retry["status"] == "success" and retry.get("adapter_status") == "success"
                for retry in item.get("recovery_attempts", [])
            )
            for item in outcomes
        )
        usable = any(
            item["status"] in {"success", "partial_success"}
            and item.get("adapter_status") == "success"
            for item in outcomes
        )
        return ExtractionResult(
            tables=tuple(tables),
            page_info=pages,
            warnings=tuple(warnings),
            manifest={
                "source_sha256": source_hash,
                "device": "cpu",
                "config": request.config.model_dump(mode="json"),
                "model_artifact_hashes": hashes,
                **timing.call("runtime_identity", runtime_manifest),
                "pipeline_options": next(reversed(used_options.values()), None),
                "pipeline_options_by_mode": used_options,
                "conversion_outcome": "success"
                if clean
                else "partial_success"
                if usable
                else "failure",
                "page_outcomes": outcomes,
                "elapsed_seconds": time.monotonic() - started,
                "peak_rss_bytes": peak[0],
                "pages": request.pages,
                "ocr": bool(ocr_pages),
                "ocr_pages": ocr_pages,
                "ocr_policy": "image_area_15_percent_native_content_20_chars_min_3pt_v2",
                "ocr_engine": "rapidocr-ppocrv6-onnx-cpu" if ocr_pages else None,
                "table_recovery_policy": "numeric_alignment_regions_v1",
                "text_visibility_policy": "pdf_rendering_modes_3_and_7_excluded_v1",
                "remote_services": False,
                "coordinate_transforms": transforms,
                "region_transform": transforms[0] if request.region and transforms else None,
                "request_region": request.region.model_dump() if request.region else None,
                "timing": timing.snapshot(),
            },
        )

    @staticmethod
    def _convert(converter, path, original_page, backend_page, input_sha256):
        try:
            result = converter.convert(
                path, page_range=(backend_page, backend_page), raises_on_error=False
            )
            return result, conversion_outcome(result, original_page, backend_page, input_sha256)
        except (MemoryError, OSError):
            raise
        except Exception as error:  # noqa: BLE001 -- retain a safe page outcome at parser boundary
            return None, {
                "original_page": original_page,
                "backend_page": backend_page,
                "input_sha256": input_sha256,
                "status": "failure",
                "errors": [],
                "exception_type": type(error).__name__,
                "confidence": None,
            }

    def _recover_marks(self, request, page, tables, source_hash):
        import numpy as np
        from PIL import Image

        if not any(not cell.text for table in tables for cell in table.content.cells):
            return tables, [], {"status": "success", "inspected": 0, "accepted": []}
        from table_review.settings import Settings

        scale = min(3, math.sqrt(Settings().max_render_pixels / (page.width * page.height)) * 0.999)
        rendered = render_page(request.source_path, page.page_no, scale)
        accepted, inspected, updated = [], 0, []
        try:
            with Image.open(rendered.image_path) as source_image:
                image = source_image.convert("RGB")
            try:
                for table in tables:
                    candidates, count = dash_candidates(
                        table.content, image, scale, limit=64 - inspected
                    )
                    inspected += count
                    replacements, new_tokens = {}, []
                    for candidate in candidates:
                        key = request.config.model_dump_json()
                        if key not in self._mark_readers:
                            self._mark_readers[key] = mark_reader(request.config)
                        window = candidate.window
                        centre = (candidate.box.left + candidate.box.right) / 2
                        width = min(
                            window.right - window.left,
                            max(
                                3 * (candidate.box.right - candidate.box.left),
                                2.4 * (window.bottom - window.top),
                            ),
                        )
                        left = max(window.left, centre - width / 2)
                        right = min(window.right, left + width)
                        crop = image.crop(
                            tuple(
                                round(v * scale) for v in (left, window.top, right, window.bottom)
                            )
                        )
                        prediction = self._mark_readers[key](
                            np.asarray(crop), use_det=False, use_cls=False
                        )
                        text = prediction.txts[0] if prediction.txts else ""
                        score = float(prediction.scores[0]) if prediction.scores else 0
                        if (
                            text not in {"-", "—", "–", "−"}
                            or not math.isfinite(score)
                            or score <= 0
                        ):
                            continue
                        reference = SourceReference(
                            page=page.page_no, precision="cell", box=candidate.box
                        )
                        replacements[candidate.cell_id] = (text, reference)
                        new_tokens.append(
                            SourceToken(
                                id=f"{source_hash}:{page.page_no}:ocr-mark:{candidate.cell_id}",
                                text=text,
                                box=candidate.box,
                                provenance=reference,
                            )
                        )
                        accepted.append(
                            {
                                "cell_id": candidate.cell_id,
                                "text": text,
                                "box": candidate.box.model_dump(mode="json"),
                                "recognizer_score": score,
                                "interpretation": "uncertain",
                            }
                        )
                    cells = tuple(
                        cell.model_copy(
                            update={
                                "raw_text": replacements[cell.id][0],
                                "text": replacements[cell.id][0],
                                "interpretation": "uncertain",
                                "reference": replacements[cell.id][1],
                            }
                        )
                        if cell.id in replacements
                        else cell
                        for cell in table.content.cells
                    )
                    updated.append(
                        table.model_copy(
                            update={
                                "content": table.content.model_copy(update={"cells": cells}),
                                "source_tokens": (*table.source_tokens, *new_tokens),
                            }
                        )
                    )
            finally:
                image.close()
        finally:
            rendered.image_path.unlink(missing_ok=True)
        warnings = (
            [
                (
                    f"Page {page.page_no}: {len(accepted)} isolated dash marks read by targeted OCR; "
                    "punctuation remains uncertain and needs image review"
                )
            ]
            if accepted
            else []
        )
        return (
            updated,
            warnings,
            {
                "status": "success",
                "policy": "isolated_horizontal_mark_ocr_v1",
                "render_scale": scale,
                "limit": 64,
                "inspected": inspected,
                "accepted": accepted,
                "score_semantics": "uncalibrated_recognizer_score_not_cell_accuracy",
                "publication_policy": "uncertain_punctuation_draft_only",
                "recognizer_model": "en_PP-OCRv5_rec_mobile.onnx",
            },
        )

    @staticmethod
    def _recover(request, page, converter, tokens, original_tables, source_hash):
        regions = tuple(table.region for table in original_tables if table.region is not None)
        # Coarse original evidence cannot safely prove a candidate is a different table.
        if any(table.region is None for table in original_tables):
            return [], [], [], []
        candidates = candidate_regions(tokens, page, regions)
        recovered, warnings, attempts, transforms = [], [], [], []
        for candidate in candidates:
            started = time.monotonic()
            region = candidate.region
            attempt = {
                "status": "pending",
                "errors": [],
                "original_page": page.page_no,
                "backend_page": 1,
                "candidate": candidate.model_dump(mode="json"),
                "stage": "automatic_region_retry",
            }
            attempts.append(attempt)
            try:
                with tempfile.TemporaryDirectory() as temporary:
                    normalized = Path(temporary) / "recovery.pdf"
                    # _vector_page mutates its reader; proposals need fresh original geometry.
                    w, h, left, top = _vector_page(
                        PdfReader(request.source_path), page, region, normalized
                    )
                    input_hash = hashlib.sha256(normalized.read_bytes()).hexdigest()
                    result, converted = DoclingExtractor._convert(
                        converter, normalized, page.page_no, 1, input_hash
                    )
                attempt.update(converted)
                transforms.append(
                    {
                        "original_page": page.page_no,
                        "backend_page": 1,
                        "offset_left": left,
                        "offset_top": top,
                        "width": w,
                        "height": h,
                        "kind": "automatic_region_retry",
                        "input_sha256": input_hash,
                    }
                )
                if attempt["status"] not in {"success", "partial_success"}:
                    warnings.append(
                        f"Page {page.page_no}: automatic region retry failed; proposed region remains unverified"
                    )
                    continue
                retry_tokens, token_warnings = ocr_tokens(
                    result, page, 1, source_hash, w, h, left, top
                )
                retry_tables, retry_warnings = DoclingExtractor._adapt_page(
                    result,
                    page,
                    1,
                    w,
                    h,
                    left,
                    top,
                    source_hash,
                    retry_tokens,
                    ocr=True,
                    identity_scope=hashlib.sha256(region.model_dump_json().encode()).hexdigest(),
                )
                accepted = [
                    table
                    for table in retry_tables
                    if table.region is not None
                    and intersects(table.region, region)
                    and table.content.rows >= 3
                    and table.content.columns >= 3
                    and not any(intersects(table.region, prior) for prior in regions)
                    and not any(intersects(table.region, prior.region) for prior in recovered)
                ]
                recovered.extend(accepted)
                warnings.extend(token_warnings)
                warnings.extend(retry_warnings)
                attempt.update(
                    adapter_status="success", accepted_table_ids=[t.id for t in accepted]
                )
                if accepted:
                    warnings.append(
                        f"Page {page.page_no}: {len(accepted)} additional table recovered automatically; "
                        "verify its structure and source image"
                    )
            except (MemoryError, OSError):
                raise
            except Exception as error:  # noqa: BLE001 -- retain a failed retry without losing primary tables
                if attempt["status"] == "pending":
                    attempt.update(status="failure", exception_type=type(error).__name__)
                attempt.update(
                    adapter_status="failure", adapter_exception_type=type(error).__name__
                )
                warnings.append(
                    f"Page {page.page_no}: automatic region retry failed; inspect parser outcomes"
                )
            finally:
                attempt["elapsed_seconds"] = time.monotonic() - started
        return recovered, warnings, attempts, transforms

    @staticmethod
    def _adapt_page(
        result,
        page,
        backend_page,
        w,
        h,
        offset_left,
        offset_top,
        source_hash,
        tokens,
        *,
        ocr=False,
        identity_scope="",
    ):
        tables, warnings = [], []
        doc_page = result.document.pages.get(backend_page)
        geometry_known = (
            doc_page is not None
            and abs(doc_page.size.width - w) < 0.1
            and abs(doc_page.size.height - h) < 0.1
        )
        if not geometry_known:
            warnings.append(f"Page {page.page_no}: extractor geometry is coarse")

        def canonical(
            box,
            page=page,
            geometry_known=geometry_known,
            h=h,
            offset_left=offset_left,
            offset_top=offset_top,
        ):
            if box is None or not geometry_known:
                return None
            box = box.to_top_left_origin(h)
            try:
                return PageBox(
                    page=page.page_no,
                    left=box.l + offset_left,
                    top=box.t + offset_top,
                    right=box.r + offset_left,
                    bottom=box.b + offset_top,
                    page_width=page.width,
                    page_height=page.height,
                )
            except ValueError:
                return None

        for index, table in enumerate(result.document.tables):
            if not table.prov or table.prov[0].page_no != backend_page:
                raise ValueError("Backend did not preserve original page numbering")
            identity = f"{source_hash}:{page.page_no}:{index}"
            table_id = str(
                uuid5(NAMESPACE_URL, f"{identity}:{identity_scope}" if identity_scope else identity)
            )
            region = canonical(table.prov[0].bbox)
            cells = []
            for ci, cell in enumerate(table.data.table_cells):
                box = canonical(cell.bbox)
                reference = SourceReference(
                    page=page.page_no,
                    precision="cell" if box else "table" if region else "page",
                    box=box or region,
                )
                role = (
                    "column_header"
                    if cell.column_header
                    else "row_header"
                    if cell.row_header
                    else "section"
                    if cell.row_section
                    else "data"
                )
                cells.append(
                    CellContent(
                        id=str(uuid5(NAMESPACE_URL, f"{table_id}:{ci}")),
                        row=cell.start_row_offset_idx,
                        col=cell.start_col_offset_idx,
                        row_span=cell.row_span,
                        col_span=cell.col_span,
                        raw_text=cell.text,
                        text=cell.text,
                        role=role,
                        reference=reference,
                    )
                )
            table_tokens = tuple(
                t
                for t in tokens
                if region is None
                or (
                    t.box.left < region.right
                    and t.box.right > region.left
                    and t.box.top < region.bottom
                    and t.box.bottom > region.top
                )
            )
            tables.append(
                ExtractedTable(
                    id=table_id,
                    page_no=page.page_no,
                    region=region,
                    content=TableContent(
                        rows=table.data.num_rows,
                        columns=table.data.num_cols,
                        cells=tuple(cells),
                        header_rows=infer_header_roles(tuple(cells))[0],
                        row_label_columns=infer_header_roles(tuple(cells))[1],
                        source_reference=SourceReference(
                            page=page.page_no,
                            precision="table" if region else "page",
                            box=region,
                        ),
                    ),
                    source_tokens=table_tokens,
                )
            )
        if not result.document.tables:
            warnings.append(f"Page {page.page_no}: no tables detected")
        for index, table in enumerate(tables):
            validate_grid(table.content)
            if not any(cell.text.strip() for cell in table.content.cells):
                raise ValueError("Extracted table contains no recognized content")
            occupied = {
                (row, col)
                for cell in table.content.cells
                for row in range(cell.row, cell.row + cell.row_span)
                for col in range(cell.col, cell.col + cell.col_span)
            }
            missing = [
                CellContent(
                    id=str(uuid5(NAMESPACE_URL, f"{table.id}:unrecognized:{row}:{col}")),
                    row=row,
                    col=col,
                    raw_text="",
                    text="",
                    interpretation="uncertain",
                    reference=table.content.source_reference,
                )
                for row in range(table.content.rows)
                for col in range(table.content.columns)
                if (row, col) not in occupied
            ]
            if missing:
                tables[index] = table.model_copy(
                    update={
                        "content": table.content.model_copy(
                            update={"cells": (*table.content.cells, *missing)}
                        )
                    }
                )
                warnings.append(
                    f"Page {page.page_no}: {'OCR' if ocr else 'Parser'} omitted "
                    f"{len(missing)} table positions; blank cells added for manual image review"
                )
        return tables, warnings
