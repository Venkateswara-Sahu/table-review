import json
import sys
import time

import pytest

from table_review.documents.preview_host import LocalPreviewHost
from table_review.jobs.runner import ProcessingFailure
from table_review.settings import Settings
from tests.fixtures.pdf_factory import two_page_report


def test_local_preview_host_reuses_warm_process_across_requests(tmp_path):
    source = two_page_report(tmp_path / "source.pdf")
    host = LocalPreviewHost(Settings(pdf_timeout_seconds=10), tmp_path)
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    request.write_text(
        json.dumps(
            {
                "kind": "info",
                "source_path": str(source),
                "page_no": 2,
                "memory_bytes": 512 * 1024**2,
            }
        ),
        encoding="utf-8",
    )
    try:
        host.run(request, result, 10)
        pid = host.process.pid
        assert json.loads(result.read_text(encoding="utf-8"))["result"]["page_no"] == 2
        result.unlink()

        render_target = tmp_path / "page2.png"
        request.write_text(
            json.dumps(
                {
                    "kind": "render",
                    "source_path": str(source),
                    "page_no": 2,
                    "scale": 1.5,
                    "target": str(render_target),
                    "max_render_pixels": 16_000_000,
                    "memory_bytes": 512 * 1024**2,
                }
            ),
            encoding="utf-8",
        )
        t0 = time.monotonic()
        host.run(request, result, 10)
        elapsed = time.monotonic() - t0
        assert host.process.pid == pid
        assert render_target.is_file()
        assert json.loads(result.read_text(encoding="utf-8"))["result"]["page_box"]["page"] == 2
        assert elapsed < 3.0
    finally:
        host.close()


def test_local_preview_host_enforces_timeout_and_recovers(tmp_path):
    fake = tmp_path / "slow.py"
    fake.write_text(
        "import sys,time\nprint('ready',flush=True)\n"
        "for _ in sys.stdin:\n    time.sleep(5)\n    print('ok',flush=True)\n",
        encoding="utf-8",
    )
    host = LocalPreviewHost(Settings(pdf_timeout_seconds=5), tmp_path)
    host.command = lambda: [sys.executable, str(fake)]
    try:
        with pytest.raises(ProcessingFailure, match="timeout"):
            host.run(tmp_path / "request.json", tmp_path / "result.json", 0.4)
        assert host.process is None
    finally:
        host.close()
