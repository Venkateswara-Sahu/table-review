import importlib.util
from pathlib import Path

import pytest


def deployment():
    spec = importlib.util.spec_from_file_location(
        "local_deployment", Path(__file__).resolve().parents[2] / "scripts/deploy_local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recorded_volume_resumes_with_overlay_and_preserves_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("TABLE_REVIEW_MODEL_VOLUME", "other-model-store")
    module = deployment()
    state = {"project": "table-review-local", "model_volume": "verified-models"}
    args = module.compose_command(state, tmp_path / "private.env")
    assert args[args.index("-p") + 1] == "table-review-local"
    assert args[-2:] == ["-f", "infra/compose.models-volume.yaml"]
    assert module.os.environ["TABLE_REVIEW_MODEL_VOLUME"] == "verified-models"


@pytest.mark.parametrize("name", ["/private/path", "volume:rw", "bad\nname", 123])
def test_model_store_rejects_paths_and_invalid_volume_names(tmp_path, name):
    with pytest.raises(SystemExit, match="Invalid recorded model volume"):
        deployment().compose_command(
            {"project": "table-review-local", "model_volume": name}, tmp_path / "private.env"
        )


def test_binding_models_uses_the_base_configuration(tmp_path):
    args = deployment().compose_command({"project": "table-review-local"}, tmp_path / "private.env")
    assert args[-2:] == ["-f", "infra/compose.yaml"]


def test_deployment_refuses_tampered_bundled_ocr_without_rewriting_identity(tmp_path):
    from table_review.runtime import write_model_manifest

    model = tmp_path / "PP-OCRv6_rec_small.onnx"
    model.write_bytes(b"frozen bundled fixture")
    write_model_manifest(tmp_path)
    lock = tmp_path / "runtime-manifest.lock"
    before = lock.read_bytes()
    model.write_bytes(b"changed bundled weights")
    with pytest.raises(SystemExit, match="recorded model identities"):
        deployment().verify_source_models(tmp_path)
    assert lock.read_bytes() == before


def test_deployment_requires_preloaded_identity_without_creating_a_new_lock(tmp_path):
    (tmp_path / "PP-OCRv6_rec_small.onnx").write_bytes(b"unrecorded weights")
    with pytest.raises(SystemExit, match="preload"):
        deployment().verify_source_models(tmp_path)
    assert not (tmp_path / "runtime-manifest.lock").exists()
