import hashlib
import json
import os
import time

import pytest

from table_review import runtime


def model_store(tmp_path):
    model = tmp_path / "weights.safetensors"
    model.write_bytes(b"frozen model bytes")
    runtime.write_model_manifest(tmp_path)
    return model


def test_unlisted_loadable_asset_is_rejected_even_with_a_valid_cached_manifest(tmp_path):
    model_store(tmp_path)
    assert runtime.models_available(tmp_path)
    (tmp_path / "unlisted.onnx").write_bytes(b"not frozen")
    with pytest.raises(ValueError, match="model identity"):
        runtime.verified_model_hashes(tmp_path)
    assert not runtime.models_available(tmp_path)


def test_manifest_cannot_omit_an_existing_loader_asset(tmp_path):
    model_store(tmp_path)
    (tmp_path / "recognizer.onnx").write_bytes(b"recognizer")
    runtime.write_model_manifest(tmp_path)
    lock = tmp_path / "runtime-manifest.lock"
    manifest = json.loads(lock.read_text(encoding="utf-8"))
    del manifest["recognizer.onnx"]
    lock.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="model identity"):
        runtime.verified_model_hashes(tmp_path)


def corrupt_preserving_mtime(model):
    before = model.stat()
    data = bytearray(model.read_bytes())
    data[-1] ^= 128
    model.write_bytes(data)
    os.utime(model, ns=(before.st_atime_ns, before.st_mtime_ns))


def freeze_model_stat(monkeypatch, model, original_stat):
    # Simulate silent corruption that the filesystem metadata cannot reveal.
    original_path_stat = type(model).stat

    def unchanged_stat(path, *args, **kwargs):
        if path == model:
            return original_stat
        return original_path_stat(path, *args, **kwargs)

    monkeypatch.setattr(type(model), "stat", unchanged_stat)


def test_fresh_verification_rejects_same_size_same_mtime_corruption(tmp_path, monkeypatch):
    model = model_store(tmp_path)
    assert runtime.models_available(tmp_path)
    original_stat = model.stat()
    corrupt_preserving_mtime(model)
    freeze_model_stat(monkeypatch, model, original_stat)
    with pytest.raises(ValueError, match="model identity"):
        runtime.verified_model_hashes(tmp_path)


def test_forced_readiness_detects_corruption_when_mtime_is_preserved(tmp_path):
    model = model_store(tmp_path)
    assert runtime.models_available(tmp_path)
    corrupt_preserving_mtime(model)
    assert not runtime.models_available(tmp_path, force_refresh=True)


def test_readiness_cache_expires_even_when_all_file_metadata_appears_unchanged(
    tmp_path, monkeypatch
):
    model = model_store(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    assert runtime.models_available(tmp_path)
    original_stat = model.stat()
    corrupt_preserving_mtime(model)
    freeze_model_stat(monkeypatch, model, original_stat)
    clock[0] += 31
    assert not runtime.models_available(tmp_path)


def test_failed_check_cannot_resurrect_an_earlier_positive_cache(tmp_path, monkeypatch):
    model = model_store(tmp_path)
    original = model.read_bytes()
    original_stat = model.stat()
    assert runtime.models_available(tmp_path)
    corrupt_preserving_mtime(model)
    freeze_model_stat(monkeypatch, model, original_stat)
    assert not runtime.models_available(tmp_path, force_refresh=True)
    # Restore metadata from the last good check, but retain corrupt bytes.
    os.utime(model, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert not runtime.models_available(tmp_path)
    model.write_bytes(original)
    assert runtime.models_available(tmp_path, force_refresh=True)
    corrupt_preserving_mtime(model)
    assert not runtime.models_available(tmp_path, force_refresh=True)
    assert not runtime.models_available(tmp_path)


def test_verified_hashes_match_frozen_manifest_and_do_not_rewrite_it(tmp_path):
    model = model_store(tmp_path)
    lock = tmp_path / "runtime-manifest.lock"
    before = lock.read_bytes()
    assert runtime.verified_model_hashes(tmp_path) == {
        "weights.safetensors": hashlib.sha256(model.read_bytes()).hexdigest()
    }
    corrupt_preserving_mtime(model)
    with pytest.raises(ValueError):
        runtime.verified_model_hashes(tmp_path)
    assert lock.read_bytes() == before


@pytest.mark.parametrize(
    "name, checksum", [("../outside.onnx", "a" * 64), ("weights.safetensors", None)]
)
def test_invalid_model_manifest_fails_closed(tmp_path, name, checksum):
    model_store(tmp_path)
    (tmp_path / "runtime-manifest.lock").write_text(json.dumps({name: checksum}))
    with pytest.raises(ValueError, match="model identity"):
        runtime.verified_model_hashes(tmp_path)
    assert not runtime.models_available(tmp_path)


@pytest.mark.parametrize("extension", [".onnx", ".json", ".safetensors", ".pt", ".bin"])
def test_unlisted_model_asset_invalidates_frozen_identity_and_readiness(tmp_path, extension):
    model_store(tmp_path)
    assert runtime.models_available(tmp_path)
    lock = tmp_path / "runtime-manifest.lock"
    before = lock.read_bytes()
    extra = tmp_path / "rapidocr" / f"unrecorded{extension}"
    extra.parent.mkdir()
    extra.write_bytes(b"unverified loader asset")
    with pytest.raises(ValueError, match="model identity"):
        runtime.verified_model_hashes(tmp_path)
    assert not runtime.models_available(tmp_path)
    assert lock.read_bytes() == before


@pytest.mark.parametrize("omitted", ["rapidocr/en_PP-OCRv5_rec_mobile.onnx", "config.json"])
def test_partial_manifest_cannot_omit_a_loader_model_or_configuration(tmp_path, omitted):
    model_store(tmp_path)
    english = tmp_path / "rapidocr/en_PP-OCRv5_rec_mobile.onnx"
    english.parent.mkdir()
    english.write_bytes(b"offline english fixture")
    (tmp_path / "config.json").write_text('{"model": "frozen"}')
    runtime.write_model_manifest(tmp_path)
    lock = tmp_path / "runtime-manifest.lock"
    manifest = json.loads(lock.read_text())
    del manifest[omitted]
    lock.write_text(json.dumps(manifest))
    before = lock.read_bytes()
    with pytest.raises(ValueError, match="model identity"):
        runtime.verified_model_hashes(tmp_path)
    assert not runtime.models_available(tmp_path)
    assert lock.read_bytes() == before


def test_cached_download_metadata_is_outside_frozen_model_inventory(tmp_path):
    model_store(tmp_path)
    cached = tmp_path / ".cache/huggingface/download.json"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"download metadata only")
    assert runtime.models_available(tmp_path, force_refresh=True)
