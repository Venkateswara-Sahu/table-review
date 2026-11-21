"""Pluggable extraction backend protocol and hybrid auto-routing dispatcher."""

import time
from typing import Protocol

from table_review.documents.pdf import inspect_pdf, page_info
from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.native_fast import NativeFastExtractor
from table_review.extraction.types import ExtractedTable, ExtractionRequest, ExtractionResult
from table_review.extraction.vlm_adapter import VlmConfig, VlmExtractor
from table_review.runtime import models_available


class ExtractorBackend(Protocol):
    name: str

    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...


def _merge_results(
    request: ExtractionRequest,
    results_by_backend: list[tuple[str, ExtractionResult]],
    started: float,
    extra_warnings: list[str],
) -> ExtractionResult:
    if len(results_by_backend) == 1 and not extra_warnings:
        backend_name, only = results_by_backend[0]
        manifest = {
            **only.manifest,
            "backend": "auto",
            "backends_used": [backend_name],
        }
        return only.model_copy(update={"manifest": manifest})

    page_info_by_no = {}
    outcome_by_page = {}
    tables_by_page: dict[int, list[ExtractedTable]] = {p: [] for p in request.pages}
    warnings: list[str] = list(extra_warnings)
    backends_used: list[str] = []
    ocr_pages: list[int] = []
    peak_rss = 0
    source_hash = None
    transforms = []
    model_hashes = None
    runtime_fields = {}

    for backend_name, res in results_by_backend:
        if backend_name not in backends_used:
            backends_used.append(backend_name)
        manifest = res.manifest
        source_hash = source_hash or manifest.get("source_sha256")
        peak_rss = max(peak_rss, int(manifest.get("peak_rss_bytes") or 0))
        ocr_pages.extend(manifest.get("ocr_pages") or ())
        transforms.extend(manifest.get("coordinate_transforms") or ())
        if manifest.get("model_artifact_hashes") is not None:
            model_hashes = manifest["model_artifact_hashes"]
        for key in (
            "code_revision",
            "code_revision_kind",
            "environment",
            "adapter_contract_version",
            "versions",
            "dependency_lock_sha256",
            "dependency_lock_available",
            "thread_environment",
        ):
            if key in manifest:
                runtime_fields[key] = manifest[key]

        for info in res.page_info:
            page_info_by_no[info.page_no] = info
        for outcome in manifest.get("page_outcomes") or ():
            pno = outcome.get("original_page")
            if pno is not None:
                outcome_by_page[pno] = {**outcome, "backend": backend_name}
        for table in res.tables:
            tables_by_page.setdefault(table.page_no, []).append(table)
        warnings.extend(res.warnings)

    ordered_tables = [t for p in request.pages for t in tables_by_page.get(p, ())]
    ordered_pages = tuple(page_info_by_no[p] for p in request.pages if p in page_info_by_no)
    ordered_outcomes = [outcome_by_page[p] for p in request.pages if p in outcome_by_page]

    clean = bool(ordered_outcomes) and all(
        item.get("status") == "success"
        and not item.get("errors")
        and item.get("adapter_status") == "success"
        and item.get("mark_recovery", {}).get("status") != "failure"
        and item.get("native_literal_reconciliation", {}).get("status") != "failure"
        and item.get("ocr_currency_reconciliation", {}).get("status") != "failure"
        for item in ordered_outcomes
    )
    usable = any(
        item.get("status") in {"success", "partial_success"}
        and item.get("adapter_status") == "success"
        for item in ordered_outcomes
    )

    merged_manifest = {
        **runtime_fields,
        "backend": "auto",
        "backends_used": backends_used,
        "source_sha256": source_hash,
        "device": next(
            (r.manifest.get("device") for _, r in results_by_backend if r.manifest.get("device")),
            "cpu",
        ),
        "config": request.config.model_dump(mode="json"),
        "conversion_outcome": "success" if clean else "partial_success" if usable else "failure",
        "page_outcomes": ordered_outcomes,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_bytes": peak_rss,
        "pages": request.pages,
        "ocr": bool(ocr_pages),
        "ocr_pages": sorted(set(ocr_pages)),
        "remote_services": any(r.manifest.get("remote_services") for _, r in results_by_backend),
        "coordinate_transforms": transforms,
        "request_region": request.region.model_dump() if request.region else None,
    }
    if model_hashes is not None:
        merged_manifest["model_artifact_hashes"] = model_hashes

    return ExtractionResult(
        tables=tuple(ordered_tables),
        page_info=ordered_pages,
        warnings=tuple(warnings),
        manifest=merged_manifest,
    )


def extract_with_backend(
    request: ExtractionRequest,
    *,
    docling_extractor: DoclingExtractor | None = None,
    vlm_config: VlmConfig | None = None,
) -> ExtractionResult:
    backend = request.config.backend
    if backend == "native_fast":
        return NativeFastExtractor().extract(request)
    if backend == "docling":
        return (docling_extractor or DoclingExtractor()).extract(request)
    if backend == "vlm":
        if vlm_config is None:
            raise ValueError(
                "VLM backend is not configured; set TABLE_REVIEW_VLM_BASE_URL and TABLE_REVIEW_VLM_MODEL"
            )
        return VlmExtractor(vlm_config).extract(request)

    started = time.monotonic()
    info = inspect_pdf(request.source_path)
    if info.encrypted:
        raise ValueError("Encrypted PDFs are unsupported")
    if max(request.pages) > info.page_count:
        raise ValueError("Selected page is outside this PDF")

    pages = tuple(page_info(request.source_path, p) for p in request.pages)
    digital_pages = tuple(
        p.page_no
        for p in pages
        if p.digital_text_present and not p.requires_ocr and not p.mixed_image_content
    )

    results_by_backend: list[tuple[str, ExtractionResult]] = []
    extra_warnings: list[str] = []
    confident_pages: set[int] = set()
    native_res: ExtractionResult | None = None

    if digital_pages:
        native_req = request.model_copy(
            update={"pages": digital_pages} if digital_pages != request.pages else {}
        )
        native_res = NativeFastExtractor().extract(native_req)
        for outcome in native_res.manifest.get("page_outcomes") or ():
            if outcome.get("confident") and outcome.get("original_page") in digital_pages:
                confident_pages.add(outcome["original_page"])
        if len(confident_pages) == len(request.pages):
            return _merge_results(request, [("native_fast", native_res)], started, extra_warnings)
        if confident_pages:
            confident_list = tuple(p for p in digital_pages if p in confident_pages)
            filtered_native = native_res.model_copy(
                update={
                    "tables": tuple(t for t in native_res.tables if t.page_no in confident_pages),
                    "page_info": tuple(p for p in native_res.page_info if p.page_no in confident_pages),
                    "manifest": {
                        **native_res.manifest,
                        "pages": confident_list,
                        "page_outcomes": [
                            o
                            for o in (native_res.manifest.get("page_outcomes") or ())
                            if o.get("original_page") in confident_pages
                        ],
                    },
                }
            )
            results_by_backend.append(("native_fast", filtered_native))

    fallback_pages = tuple(p.page_no for p in pages if p.page_no not in confident_pages)
    if not fallback_pages:
        return _merge_results(request, results_by_backend, started, extra_warnings)

    if models_available(request.config.artifacts_path):
        fallback_req = request.model_copy(
            update={
                "pages": fallback_pages,
                "region": request.region if len(fallback_pages) == 1 else None,
            }
        )
        docling_res = (docling_extractor or DoclingExtractor()).extract(fallback_req)
        results_by_backend.append(("docling", docling_res))
        return _merge_results(request, results_by_backend, started, extra_warnings)

    vlm_allowed = vlm_config is not None and (
        vlm_config.deployment_mode != "hosted" or vlm_config.allow_vlm_in_hosted
    )
    if vlm_allowed:
        fallback_req = request.model_copy(
            update={
                "pages": fallback_pages,
                "region": request.region if len(fallback_pages) == 1 else None,
            }
        )
        vlm_res = VlmExtractor(vlm_config).extract(fallback_req)
        results_by_backend.append(("vlm", vlm_res))
        return _merge_results(request, results_by_backend, started, extra_warnings)

    # Docling models unavailable & no VLM configured: retain non-confident native digital results if possible.
    if native_res is not None and set(fallback_pages) <= set(digital_pages):
        fallback_set = set(fallback_pages)
        retained = native_res.model_copy(
            update={
                "tables": tuple(t for t in native_res.tables if t.page_no in fallback_set),
                "page_info": tuple(p for p in native_res.page_info if p.page_no in fallback_set),
                "manifest": {
                    **native_res.manifest,
                    "pages": fallback_pages,
                    "page_outcomes": [
                        o
                        for o in (native_res.manifest.get("page_outcomes") or ())
                        if o.get("original_page") in fallback_set
                    ],
                },
            }
        )
        extra_warnings.append("Docling models unavailable; retained fast native extraction")
        results_by_backend.append(("native_fast", retained))
        return _merge_results(request, results_by_backend, started, extra_warnings)

    raise ValueError("Offline model identity is unavailable")
