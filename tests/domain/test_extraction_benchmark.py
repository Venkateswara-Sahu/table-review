import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def benchmark_module():
    spec = importlib.util.spec_from_file_location(
        "extraction_benchmark",
        Path(__file__).resolve().parents[2] / "scripts/benchmark_extraction.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_completed_cold_benchmark_survives_reused_context_failure(tmp_path, monkeypatch):
    import table_review.extraction.docling_adapter as adapter

    class Extractor:
        calls = 0

        def extract(self, request):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("private report contents")
            return SimpleNamespace(tables=(), manifest={"conversion_outcome": "success"})

    monkeypatch.setattr(adapter, "DoclingExtractor", Extractor)
    output = tmp_path / "observations.json"
    with pytest.raises(RuntimeError):
        benchmark_module().sample(
            SimpleNamespace(source=tmp_path / "source.pdf", models=tmp_path, page=1, output=output)
        )
    saved = json.loads(output.read_text())
    assert len(saved["runs"]) == 1
    assert saved["runs"][0]["context_reused"] is False
    assert saved["active_context_reused"] is True
    assert "private report contents" not in output.read_text()


def test_benchmark_controller_retains_partial_samples_and_reports_failure(tmp_path, monkeypatch):
    from table_review.jobs import runner

    module = benchmark_module()
    output = tmp_path / "results.json"

    def fail(command, *args, **kwargs):
        Path(command[-1]).write_text(
            json.dumps(
                {
                    "runs": [{"context_reused": False, "seconds": 0.5}],
                    "active_context_reused": True,
                }
            )
        )
        raise runner.ProcessingFailure("timeout")

    monkeypatch.setattr(runner, "run_child", fail)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "benchmark",
            "--source",
            str(tmp_path / "source.pdf"),
            "--page",
            "1",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 1
    pairs = json.loads(output.read_text())["pairs"]
    assert len(pairs) == 3
    assert all(pair["failure_code"] == "timeout" for pair in pairs)
    assert all(pair["failed_context_reused"] is True for pair in pairs)
    assert all(pair["runs"] == [{"context_reused": False, "seconds": 0.5}] for pair in pairs)


def test_interrupted_checkpoint_keeps_the_previous_observation(tmp_path, monkeypatch):
    module = benchmark_module()
    output = tmp_path / "observations.json"
    previous = {"runs": [{"context_reused": False}], "active_context_reused": True}
    output.write_text(json.dumps(previous))

    def interrupt(*args):
        raise OSError("interrupted atomic publication")

    monkeypatch.setattr(module.os, "replace", interrupt)
    with pytest.raises(OSError):
        module.checkpoint(output, {"runs": [], "active_context_reused": False})
    assert json.loads(output.read_text()) == previous


def test_unreadable_checkpoint_is_explicitly_unavailable(tmp_path):
    module = benchmark_module()
    output = tmp_path / "observations.json"
    output.write_text('{"runs": [')
    assert module.read_checkpoint(output) is None
