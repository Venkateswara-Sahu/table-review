import json
import sys
from contextlib import contextmanager
from threading import Semaphore

from table_review.documents.errors import DocumentError
from table_review.documents.preview_host import PreviewHost
from table_review.documents.types import PageInfo, PdfInfo, RenderedPage
from table_review.jobs.runner import ProcessingFailure, run_child
from table_review.jobs.types import ProcessingLimits
from table_review.storage.scratch import upload_scratch


class PdfOperations:
    """One terminable native operation, finite admission wait, private scratch."""

    def __init__(self, settings, scratch):
        self.settings, self.scratch = settings, scratch
        self.timeout_seconds = settings.pdf_timeout_seconds
        self.slot = Semaphore(1)
        self.host = PreviewHost(settings, scratch) if sys.platform == "linux" else None

    def warmup(self):
        if self.host is not None:
            with self.slot:
                self.host.warmup()

    def close(self):
        if self.host is not None:
            with self.slot:
                self.host.close()

    def command(self, request, result):
        return [sys.executable, "-m", "table_review.documents.pdf_child", str(request), str(result)]

    @contextmanager
    def _operation(self, kind, source, **extra):
        if not self.slot.acquire(timeout=self.settings.pdf_wait_seconds):
            raise DocumentError("pdf_busy", "Another PDF operation is busy; retry shortly", 429)
        try:
            with upload_scratch(self.scratch) as directory:
                request, result = directory / "request.json", directory / "result.json"
                payload = {
                    "kind": kind,
                    "source_path": str(source),
                    "memory_bytes": self.settings.pdf_memory_bytes,
                    **extra,
                }
                if kind == "render":
                    payload["target"] = str(directory / "page.png")
                    payload["max_render_pixels"] = self.settings.max_render_pixels
                request.write_text(json.dumps(payload), encoding="utf-8")
                try:
                    command = self.command(request, result)
                    # Custom commands used for isolation checks keep the
                    # standalone runner; native previews use fresh forked
                    # children of a trusted, preloaded Linux template.
                    if (
                        self.host is not None
                        and kind in ("info", "render")
                        and command == PdfOperations.command(self, request, result)
                    ):
                        self.host.run(request, result, self.timeout_seconds)
                    else:
                        run_child(
                            command,
                            ProcessingLimits(
                                timeout_seconds=self.timeout_seconds,
                                memory_bytes=self.settings.pdf_memory_bytes,
                                poll_seconds=0.02,
                            ),
                            work_dir=directory,
                        )
                except ProcessingFailure as error:
                    raise DocumentError(
                        "pdf_" + error.code,
                        "PDF operation could not be completed within its processing limits",
                    ) from error
                if not result.is_file():
                    raise DocumentError(
                        "pdf_unavailable", "PDF processing result is unavailable", 503
                    )
                data = json.loads(result.read_text(encoding="utf-8"))
                if "failure_code" in data:
                    raise DocumentError(
                        data["failure_code"],
                        "PDF exceeds a limit or could not be processed",
                        data["status"],
                    )
                yield data["result"]
        finally:
            self.slot.release()

    def inspect(self, source):
        with self._operation("inspect", source, max_pdf_pages=self.settings.max_pdf_pages) as data:
            return PdfInfo.model_validate(data)

    def info(self, source, page_no):
        with self._operation("info", source, page_no=page_no) as data:
            return PageInfo.model_validate(data)

    @contextmanager
    def render(self, source, page_no, scale):
        with self._operation("render", source, page_no=page_no, scale=scale) as data:
            yield RenderedPage.model_validate(data)
