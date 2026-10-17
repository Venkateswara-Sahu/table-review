import json
import sys
import time

import pytest

from table_review.documents.preview_host import PreviewHost
from table_review.jobs.runner import ProcessingFailure
from table_review.settings import Settings
from tests.fixtures.pdf_factory import two_page_report

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux isolated preview template")


def test_preview_template_reuses_libraries_across_fresh_native_operations(tmp_path):
    source = two_page_report(tmp_path / "source.pdf")
    host = PreviewHost(Settings(pdf_timeout_seconds=5), tmp_path)
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    request.write_text(
        json.dumps(
            {
                "kind": "info",
                "source_path": str(source),
                "page_no": 2,
                "memory_bytes": 512 * 1024**2,
            }
        )
    )
    try:
        host.run(request, result, 5)
        pid = host.process.pid
        assert json.loads(result.read_text())["result"]["page_no"] == 2
        result.unlink()
        host.run(request, result, 5)
        assert host.process.pid == pid
        assert json.loads(result.read_text())["result"]["digital_text_present"]
    finally:
        host.close()


def test_cold_template_and_operation_share_one_timeout_budget(tmp_path):
    fake = tmp_path / "delayed.py"
    fake.write_text(
        "import sys,time\ntime.sleep(0.4)\nprint('ready',flush=True)\n"
        "for line in sys.stdin:\n    time.sleep(0.4)\n    print('ok',flush=True)\n"
    )
    host = PreviewHost(Settings(pdf_timeout_seconds=5), tmp_path)
    host.command = lambda: [sys.executable, str(fake)]
    start = time.monotonic()
    try:
        with pytest.raises(ProcessingFailure, match="timeout"):
            host.run(tmp_path / "request.json", tmp_path / "result.json", 0.6)
    finally:
        host.close()
    assert time.monotonic() - start < 1.2


def test_partial_protocol_line_cannot_block_timeout(tmp_path):
    fake = tmp_path / "partial.py"
    fake.write_text(
        "import os,sys,time\nprint('ready',flush=True)\n"
        "for line in sys.stdin:\n    os.write(1,b'partial')\n    time.sleep(60)\n"
    )
    host = PreviewHost(Settings(pdf_timeout_seconds=5), tmp_path)
    host.command = lambda: [sys.executable, str(fake)]
    host.warmup()
    try:
        with pytest.raises(ProcessingFailure, match="timeout"):
            host.run(tmp_path / "request.json", tmp_path / "result.json", 0.6)
    finally:
        host.close()
