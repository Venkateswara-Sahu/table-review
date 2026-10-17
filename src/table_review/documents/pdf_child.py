"""Small private PDF protocol; native work runs outside the serving process."""

import json
import sys
from pathlib import Path

from pypdfium2 import PdfiumError

from table_review.documents.errors import DocumentError
from table_review.documents.pdf_native import page_info, render_page


def inspect_supported(path, settings):
    from pypdf.errors import PdfReadError

    from table_review.documents.pdf import inspect_pdf

    try:
        info = inspect_pdf(path)
    except PdfReadError as error:
        raise DocumentError("invalid_pdf", "The PDF could not be read") from error
    except ValueError as error:
        code = (
            "page_limit"
            if "500 page" in str(error)
            else "byte_limit"
            if "50 MiB" in str(error)
            else "invalid_pdf"
        )
        raise DocumentError(
            code,
            "The PDF exceeds a limit or could not be read",
            413 if code == "byte_limit" else 422,
        ) from error
    if info.encrypted:
        raise DocumentError(
            "encrypted_pdf", "Encrypted PDFs are unsupported; upload an unencrypted copy"
        )
    if not info.page_count:
        raise DocumentError("invalid_pdf", "PDF contains no pages")
    if info.page_count > settings.max_pdf_pages:
        raise DocumentError("page_limit", "PDF exceeds the configured page limit")
    return info


def process(payload):
    path = Path(payload["source_path"])
    if payload["kind"] == "inspect":
        from table_review.settings import Settings

        return inspect_supported(path, Settings(max_pdf_pages=payload["max_pdf_pages"])).model_dump(
            mode="json"
        )
    if payload["kind"] == "info":
        return page_info(path, payload["page_no"])
    if payload["kind"] == "render":
        rendered = render_page(
            path, payload["page_no"], payload["scale"], payload.get("max_render_pixels", 16_000_000)
        )
        destination = Path(payload["target"])
        Path(rendered["image_path"]).replace(destination)
        return {**rendered, "image_path": str(destination)}
    raise ValueError("Unsupported PDF operation")


def apply_memory_limit(memory_bytes):
    # Trusted module imports precede this; no PDF bytes are parsed before it.
    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def run_request(request, destination):
    try:
        payload = json.loads(request.read_text(encoding="utf-8"))
        apply_memory_limit(payload["memory_bytes"])
        result = {"result": process(payload)}
    except DocumentError as error:
        result = {"failure_code": error.code, "status": error.status}
    except (PdfiumError, ValueError):
        result = {"failure_code": "page_unavailable", "status": 422}
    except MemoryError:
        result = {"failure_code": "resource_exhaustion", "status": 422}
    except Exception:  # noqa: BLE001 - no source-bearing traceback crosses the protocol
        result = {"failure_code": "pdf_unavailable", "status": 503}
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(destination)


def main():
    run_request(*map(Path, sys.argv[1:3]))


if __name__ == "__main__":
    main()
