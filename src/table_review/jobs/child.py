"""Private subprocess protocol. Errors never serialize source content or tracebacks."""

import json
import os
import sys
from pathlib import Path

from pypdf.errors import PdfReadError

from table_review.documents.errors import DocumentError
from table_review.extraction.types import ExtractionRequest
from table_review.settings import Settings


def _env_vlm_config():
    base_url = os.environ.get("TABLE_REVIEW_VLM_BASE_URL")
    model = os.environ.get("TABLE_REVIEW_VLM_MODEL")
    if not base_url or not model:
        return None
    from table_review.extraction.vlm_adapter import VlmConfig

    return VlmConfig(
        base_url=base_url,
        model=model,
        api_key=os.environ.get("TABLE_REVIEW_VLM_API_KEY") or None,
        timeout_seconds=float(os.environ.get("TABLE_REVIEW_VLM_TIMEOUT_SECONDS", "120.0")),
        deployment_mode=os.environ.get("TABLE_REVIEW_DEPLOYMENT_MODE", "local"),
        allow_vlm_in_hosted=(
            os.environ.get("TABLE_REVIEW_ALLOW_VLM_IN_HOSTED", "false").lower() == "true"
        ),
    )


def process(payload, *, docling_extractor=None):
    if payload["kind"] == "export":
        from table_review.exports.csv_package import write_csv_package
        from table_review.exports.types import ExportInput, ReportExportInput
        from table_review.exports.workbook import write_report_workbook, write_workbook

        fmt = payload["format"]
        if fmt == "report_workbook":
            return write_report_workbook(
                ReportExportInput.model_validate(payload["snapshot"]), Path(payload["target"])
            ).model_dump(mode="json")
        writer = write_workbook if fmt == "workbook" else write_csv_package
        return writer(
            ExportInput.model_validate(payload["snapshot"]), Path(payload["target"])
        ).model_dump(mode="json")
    if payload["kind"] == "prepare":
        from table_review.documents.pdf_child import inspect_supported

        return inspect_supported(Path(payload["source_path"]), Settings()).model_dump(mode="json")
    if payload["kind"] == "extract":
        from table_review.extraction.backends import extract_with_backend

        request = ExtractionRequest.model_validate(payload["request"])
        vlm_config = _env_vlm_config() if request.config.backend in ("vlm", "auto") else None
        return extract_with_backend(
            request, docling_extractor=docling_extractor, vlm_config=vlm_config
        ).model_dump(mode="json")
    raise ValueError("Unsupported task kind")


def execute_file(source: Path, destination: Path, *, docling_extractor=None) -> dict:
    try:
        result = process(
            json.loads(source.read_text(encoding="utf-8")), docling_extractor=docling_extractor
        )
    except DocumentError as error:
        result = {"failure_code": error.code, "error_type": type(error).__name__}
    except (PdfReadError, ValueError) as error:
        result = {
            "failure_code": "unsupported_page" if "digital text" in str(error) else "parse_failure",
            "error_type": type(error).__name__,
        }
    except MemoryError as error:
        result = {"failure_code": "resource_exhaustion", "error_type": type(error).__name__}
    except OSError as error:
        result = {"failure_code": "storage_failure", "error_type": type(error).__name__}
    except Exception as error:  # noqa: BLE001 -- untrusted parser boundary, never expose source-bearing errors
        result = {"failure_code": "parse_failure", "error_type": type(error).__name__}
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(destination)
    return result


def main():
    source, destination = map(Path, sys.argv[1:3])
    execute_file(source, destination)


if __name__ == "__main__":
    main()
