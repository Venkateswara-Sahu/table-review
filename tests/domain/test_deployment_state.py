"""Operator publication must record the schema actually read from the deployed DB."""

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from tests.domain.test_deployment_model_store import deployment


def prepare_operator(tmp_path, monkeypatch, schema_revision):
    module = deployment()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(module, "__file__", str(scripts / "deploy_local.py"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["deploy_local.py"])
    cache = tmp_path / ".cache"
    cache.mkdir()
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/model-artifacts.json").write_text('{"profiles": []}')
    model = tmp_path / "models/rapidocr/mark.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"pinned test model")
    (tmp_path / "infra/ocr-mark-model.json").write_text(
        json.dumps({"name": model.name, "sha256": hashlib.sha256(model.read_bytes()).hexdigest()})
    )
    (cache / "local-project.json").write_text(
        json.dumps({"project": "table-review-local", "url": "http://127.0.0.1:8000", "port": 8000})
    )
    state_file = cache / "local-state.json"
    state_file.write_text(json.dumps({"schema_revision": "0011", "saved_marker": "preserved"}))
    monkeypatch.setattr(module, "verify_source_models", lambda root: None)
    monkeypatch.setattr(
        module.httpx, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200)
    )
    commands = []

    def command(args, **kwargs):
        commands.append(args)
        if args[:3] == ["docker", "volume", "ls"]:
            return "saved-local-volume\n"
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(
                [
                    {
                        "HostConfig": {"NetworkMode": "none", "Memory": 6 * 1024**3},
                        "Config": {"User": "10001:10001"},
                        "Image": "sha256:fixture",
                    }
                ]
            )
        if args[:2] == ["docker", "exec"]:
            return schema_revision if "alembic_version" in args[-1] else "fixture-adapter"
        return ""

    monkeypatch.setattr(module, "command", command)
    return module, state_file, commands


@pytest.mark.parametrize("revision", ["0013", "0014"])
def test_deployment_records_live_schema_instead_of_retaining_stale_metadata(
    tmp_path, monkeypatch, revision
):
    module, state_file, commands = prepare_operator(tmp_path, monkeypatch, revision)
    module.main()
    saved = json.loads(state_file.read_text())
    assert saved["schema_revision"] == revision
    assert saved["saved_marker"] == "preserved"
    assert any("alembic_version" in args[-1] for args in commands)
    assert all(not (args[:2] == ["docker", "compose"] and "down" in args) for args in commands)


@pytest.mark.parametrize("revision", ["", "0013\n0014"])
def test_unavailable_or_ambiguous_schema_cannot_publish_a_verified_state(
    tmp_path, monkeypatch, revision
):
    module, state_file, _ = prepare_operator(tmp_path, monkeypatch, revision)
    previous = state_file.read_bytes()
    with pytest.raises(SystemExit, match="schema revision"):
        module.main()
    assert state_file.read_bytes() == previous
