import sys
import time
from types import SimpleNamespace

import pytest

from table_review.extraction import docling_adapter
from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.types import ExtractionRequest, ExtractorConfig
from table_review.jobs.runner import ProcessingFailure
from table_review.jobs.types import ProcessingLimits
from table_review.jobs.warm_worker import WarmChildHost
from table_review.runtime import write_model_manifest
from tests.fixtures.pdf_factory import two_page_report


def test_warm_child_host_reuses_pid_across_prepare_jobs(tmp_path):
    source = two_page_report(tmp_path / "warm.pdf")
    host = WarmChildHost(tmp_path / "warm_scratch")
    limits = ProcessingLimits(timeout_seconds=15, memory_bytes=1024 * 1024**2, poll_seconds=0.02)
    try:
        r1 = host.run(
            {"kind": "prepare", "source_path": str(source)},
            limits,
            work_dir=tmp_path / "attempt1",
        )
        first_pid = host.pid
        assert first_pid is not None
        assert r1["page_count"] == 2

        r2 = host.run(
            {"kind": "prepare", "source_path": str(source)},
            limits,
            work_dir=tmp_path / "attempt2",
        )
        assert host.pid == first_pid
        assert r2["page_count"] == 2
    finally:
        host.close()
    assert host.pid is None


def test_warm_child_host_recovers_after_timeout_or_cancel(tmp_path):
    fake_worker = tmp_path / "fake_warm.py"
    fake_worker.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "print(json.dumps({'event': 'ready'}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    cmd = json.loads(line)\n"
        "    req = json.loads(Path(cmd['request_path']).read_text())\n"
        "    if req.get('sleep'):\n"
        "        time.sleep(req['sleep'])\n"
        "    Path(cmd['result_path']).write_text(json.dumps({'ok': True, 'val': req.get('val')}))\n"
        "    print(json.dumps({'event': 'done'}), flush=True)\n",
        encoding="utf-8",
    )
    host = WarmChildHost(tmp_path / "scratch", command=[sys.executable, str(fake_worker)])
    fast_limits = ProcessingLimits(timeout_seconds=0.5, memory_bytes=512 * 1024**2, poll_seconds=0.02)
    try:
        res1 = host.run({"kind": "prepare", "val": 1}, fast_limits, work_dir=tmp_path / "w1")
        pid1 = host.pid
        assert res1["val"] == 1

        with pytest.raises(ProcessingFailure, match="timeout"):
            host.run({"kind": "prepare", "sleep": 5.0}, fast_limits, work_dir=tmp_path / "w2")
        assert host.pid is None

        # Next job respawns cleanly on a new PID.
        res3 = host.run({"kind": "prepare", "val": 3}, fast_limits, work_dir=tmp_path / "w3")
        assert res3["val"] == 3
        assert host.pid is not None and host.pid != pid1

        # Cancellation kills and resets cleanly too.
        calls = 0

        def cancel_soon():
            nonlocal calls
            calls += 1
            return calls >= 2

        with pytest.raises(ProcessingFailure, match="cancelled"):
            host.run(
                {"kind": "prepare", "sleep": 5.0},
                ProcessingLimits(timeout_seconds=5, memory_bytes=512 * 1024**2, poll_seconds=0.02),
                work_dir=tmp_path / "w4",
                cancelled=cancel_soon,
            )
        assert host.pid is None
    finally:
        host.close()


def test_docling_warm_model_cache_verifies_once_and_invalidates_on_change(tmp_path, monkeypatch):
    source = two_page_report(tmp_path / "docling.pdf")
    models = tmp_path / "models"
    models.mkdir()
    weights = models / "weights.safetensors"
    weights.write_bytes(b"weights-v1")
    write_model_manifest(models)

    calls = 0
    real_model_hashes = docling_adapter.model_hashes

    def spy_model_hashes(root: str):
        nonlocal calls
        calls += 1
        return real_model_hashes(root)

    class FakeConverter:
        def convert(self, path, *, page_range, raises_on_error=True):
            page = page_range[0]
            return SimpleNamespace(
                status="success",
                errors=[],
                confidence=SimpleNamespace(model_dump=lambda **kw: {"pages": {}}),
                document=SimpleNamespace(
                    pages={page: SimpleNamespace(size=SimpleNamespace(width=612, height=792))},
                    tables=[],
                ),
            )

    monkeypatch.setattr(docling_adapter, "model_hashes", spy_model_hashes)
    monkeypatch.setattr(DoclingExtractor, "_converter", lambda *args, **kwargs: FakeConverter())

    extractor = DoclingExtractor(warm_model_cache=True)
    req = ExtractionRequest(
        source_path=source,
        pages=(2,),
        config=ExtractorConfig(artifacts_path=models, backend="docling"),
    )
    extractor.extract(req)
    assert calls == 1

    # Unchanged model stat signature reuses verified hashes without re-reading model bytes.
    extractor.extract(req)
    assert calls == 1

    # Tampering the model file invalidates the stat signature and triggers re-verification (and fails closed).
    time.sleep(0.02)
    weights.write_bytes(b"weights-v2-longer")
    with pytest.raises(ValueError, match="model identity"):
        extractor.extract(req)
    assert calls == 2
