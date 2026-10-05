"""Private subprocess protocol. Errors never serialize source content or tracebacks."""

import json
import sys
from pathlib import Path

from pypdf.errors import PdfReadError

from table_review.documents.service import DocumentError, DocumentService
from table_review.extraction.types import ExtractionRequest
from table_review.settings import Settings


def process(payload):
    if payload["kind"] == "prepare":
        from table_review.storage.blobs import PrivateBlobStore
        from table_review.storage.repository import Repository

        service = DocumentService(
            Repository(None), PrivateBlobStore(Path(payload["scratch"])), Settings()
        )
        return service._inspect(Path(payload["source_path"])).model_dump(mode="json")
    if payload["kind"] == "extract":
        from table_review.extraction.docling_adapter import DoclingExtractor

        request = ExtractionRequest.model_validate(payload["request"])
        return DoclingExtractor().extract(request).model_dump(mode="json")
    raise ValueError("Unsupported task kind")


def main():
    source, destination = map(Path, sys.argv[1:3])
    try:
        result = process(json.loads(source.read_text(encoding="utf-8")))
    except DocumentError as error:
        result = {"failure_code": error.code}
    except (PdfReadError, ValueError) as error:
        result = {
            "failure_code": "unsupported_page" if "digital text" in str(error) else "parse_failure"
        }
    except MemoryError:
        result = {"failure_code": "resource_exhaustion"}
    except OSError:
        result = {"failure_code": "storage_failure"}
    except Exception:  # noqa: BLE001 -- untrusted parser boundary, never expose source-bearing errors
        result = {"failure_code": "parse_failure"}
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(destination)


if __name__ == "__main__":
    main()
