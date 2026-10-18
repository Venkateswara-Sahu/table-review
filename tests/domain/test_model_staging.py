import importlib.util
import os
from pathlib import Path

import pytest


def stager():
    spec = importlib.util.spec_from_file_location(
        "model_staging", Path(__file__).resolve().parents[2] / "scripts/stage_offline_models.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.stage


def source_model(tmp_path):
    from table_review.runtime import write_model_manifest

    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "model.onnx").write_bytes(b"frozen offline fixture")
    write_model_manifest(source)
    return source, destination


def test_staging_verifies_bytes_and_preserves_source_and_existing_destination(tmp_path):
    source, destination = source_model(tmp_path)
    before = (source / "model.onnx").read_bytes()
    summary = stager()(source, destination)
    assert summary["verified"] is True and summary["model_count"] == 1
    assert (destination / "model.onnx").read_bytes() == before
    assert (source / "model.onnx").read_bytes() == before
    assert stager()(source, destination) == summary


def test_staging_rejects_conflicting_destination_without_overwriting(tmp_path):
    source, destination = source_model(tmp_path)
    (destination / "model.onnx").write_bytes(b"existing different model")
    with pytest.raises(ValueError, match="conflict"):
        stager()(source, destination)
    assert (destination / "model.onnx").read_bytes() == b"existing different model"


def test_staging_rejects_unverified_source_before_copying(tmp_path):
    source, destination = source_model(tmp_path)
    (source / "model.onnx").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="identity"):
        stager()(source, destination)
    assert list(destination.iterdir()) == []


def test_staging_rechecks_cached_source_before_copying_any_bytes(tmp_path):
    from table_review.runtime import models_available

    source, destination = source_model(tmp_path)
    model = source / "model.onnx"
    assert models_available(source)
    before = model.stat()
    model.write_bytes(b"X" * before.st_size)
    os.utime(model, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="identity"):
        stager()(source, destination)
    assert list(destination.iterdir()) == []
