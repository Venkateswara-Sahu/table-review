"""JSON-safe parser diagnostics and the installed extraction runtime identity."""

import hashlib
import importlib.metadata
import math
import os
from collections.abc import Mapping
from enum import Enum
from pathlib import Path

from table_review.jobs.runner import code_identity

ADAPTER_CONTRACT_VERSION = "docling-auto-ocr-source-literals-cpu-v7"


def json_safe(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    return value


def conversion_outcome(result, original_page, backend_page, input_sha256):
    errors = []
    for error in result.errors:
        errors.append(
            {
                "component_type": json_safe(error.component_type),
                "module_name": error.module_name,
                "category": json_safe(error.category),
                "backend_page": error.page_no,
                "original_page": original_page if error.page_no == backend_page else None,
                # Parser messages can contain source literals and private paths. Retain identity,
                # category and location, never echo arbitrary parser text into API/export/logs.
                "message_sha256": hashlib.sha256(error.error_message.encode("utf-8")).hexdigest(),
                "message_redacted": True,
            }
        )
    return {
        "original_page": original_page,
        "backend_page": backend_page,
        "input_sha256": input_sha256,
        "status": json_safe(result.status),
        "errors": errors,
        "confidence": json_safe(result.confidence.model_dump(mode="json")),
        "confidence_semantics": "uncalibrated_parser_diagnostics_not_cell_accuracy",
        "parser_timings": parser_timings(result),
        "parser_timing_semantics": "wall_durations_may_overlap_not_additive",
    }


def parser_timings(result):
    allowed = {
        "page_parse",
        "layout",
        "layout_postprocess",
        "ocr",
        "table_structure",
        "doc_build",
        "doc_assemble",
        "doc_enrich",
        "pipeline_total",
    }
    values = {}
    timings = getattr(result, "timings", None)
    if not isinstance(timings, Mapping):
        return values
    for name in sorted(allowed):
        item = timings.get(name)
        samples = getattr(item, "times", ())
        if not isinstance(samples, (tuple, list)) or not samples or len(samples) > 1000:
            continue
        try:
            if not all(
                type(value) in (int, float) and math.isfinite(value) and value >= 0
                for value in samples
            ):
                continue
            total = float(sum(samples))
        except (OverflowError, ValueError, TypeError):
            continue
        if math.isfinite(total):
            values[name] = {"seconds": total, "count": len(samples)}
    return values


def runtime_manifest():
    versions = {
        distribution.metadata["Name"].lower().replace("_", "-"): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }
    # Locate the lock for source and packaged installs without depending on process cwd.
    lock = next(
        (
            parent / "uv.lock"
            for parent in Path(__file__).resolve().parents
            if (parent / "uv.lock").is_file()
        ),
        None,
    )
    return {
        **code_identity(),
        "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
        "versions": dict(sorted(versions.items())),
        "dependency_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest() if lock else None,
        "dependency_lock_available": lock is not None,
        "thread_environment": {
            key: os.getenv(key)
            for key in (
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
            )
        },
    }
