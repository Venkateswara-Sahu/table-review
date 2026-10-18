import importlib.util
from pathlib import Path
from types import SimpleNamespace

from tests.domain.test_deployment_model_store import deployment


def rehearsal():
    spec = importlib.util.spec_from_file_location(
        "https_rehearsal", Path(__file__).resolve().parents[2] / "scripts/rehearse_https.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_command_can_receive_a_frozen_environment(monkeypatch):
    module = deployment()
    observed = {}

    def run(*args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert (
        module.command(["docker", "compose", "config"], env={"TABLE_REVIEW_BIND_IP": "127.0.0.1"})
        == "ok"
    )
    assert observed["env"] == {"TABLE_REVIEW_BIND_IP": "127.0.0.1"}


def test_rehearsal_environment_overrides_public_shell_settings(monkeypatch):
    module = rehearsal()
    monkeypatch.setenv("TABLE_REVIEW_BIND_IP", "0.0.0.0")
    monkeypatch.setenv("TABLE_REVIEW_TLS_MODE", "public_tls")
    monkeypatch.setenv("POSTGRES_PASSWORD", "wrong-shell-fixture")
    overrides = module.rehearsal_overrides(51888, 51889)
    env = module.frozen_environment("POSTGRES_PASSWORD=private-fixture\n", overrides)
    assert env["TABLE_REVIEW_BIND_IP"] == "127.0.0.1"
    assert env["TABLE_REVIEW_TLS_MODE"] == "rehearsal_tls"
    assert env["TABLE_REVIEW_HOSTNAME"] == "localhost"
    assert env["POSTGRES_PASSWORD"] == "private-fixture"
    assert env["TABLE_REVIEW_PUBLIC_ORIGIN"] == "https://localhost:51888"


def test_rehearsal_rejects_the_normal_project():
    import pytest

    with pytest.raises(ValueError, match="isolated acceptance"):
        rehearsal().isolated({"project": "table-review-local"})
