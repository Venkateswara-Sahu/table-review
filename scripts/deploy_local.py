"""Start a private local deployment; never publish or destroy saved volumes."""

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from table_review.runtime import models_available


def command(args, *, timeout=600):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise SystemExit("Local Docker operation failed; inspect sanitized service readiness/logs")
    return result.stdout


def compose_command(state, env_file):
    args = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-p",
        state["project"],
        "-f",
        "infra/compose.yaml",
    ]
    volume = state.get("model_volume")
    if volume:
        if not isinstance(volume, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", volume
        ):
            raise SystemExit("Invalid recorded model volume name")
        os.environ["TABLE_REVIEW_MODEL_VOLUME"] = volume
        args += ["-f", "infra/compose.models-volume.yaml"]
    return args


def verify_source_models(root):
    if not (root / "runtime-manifest.lock").is_file():
        raise SystemExit("Run the preload commands to record offline model identities first")
    if not models_available(root):
        raise SystemExit("Preloaded weights differ from recorded model identities")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance", action="store_true")
    parser.add_argument("--fresh-acceptance", action="store_true")
    parser.add_argument("--stop", action="store_true")
    storage = parser.add_mutually_exclusive_group()
    storage.add_argument("--model-volume", help="Existing checksum-verified offline model volume")
    storage.add_argument(
        "--bind-models", action="store_true", help="Return to the original model bind mount"
    )
    args = parser.parse_args()
    if args.stop and (args.model_volume or args.bind_models):
        parser.error("Stopping does not change model storage")
    if args.fresh_acceptance:
        if args.stop:
            parser.error("Fresh initialization and stopping are separate actions")
        args.acceptance = True
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    prefix = "runtime" if args.acceptance else "local"
    project_file = cache / (prefix + "-project.json")
    env_file = cache / (prefix + ".env")
    if args.fresh_acceptance and project_file.exists():
        prior = json.loads(project_file.read_text(encoding="utf-8"))
        if not re.fullmatch(r"table-review-acceptance-[a-f0-9]{32}", prior["project"]):
            raise SystemExit("Invalid isolated acceptance project")
        saved = cache / "runtime-history" / uuid4().hex
        saved.mkdir(parents=True, mode=0o700)
        records = [project_file, env_file, cache / "runtime-state.json"]
        for path in records:
            if path.exists():
                shutil.copyfile(path, saved / path.name)
                (saved / path.name).chmod(0o600)
        command(compose_command(prior, saved / env_file.name) + ["stop"])
        # Only pointers to the archived test deployment are replaced. Its
        # named volumes and private configuration remain recoverable.
        for path in records:
            path.unlink(missing_ok=True)
    if not project_file.exists():
        if args.stop:
            raise SystemExit("No recorded local project to stop")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1] if args.acceptance else 8000
        state = {
            "project": "table-review-acceptance-" + uuid4().hex
            if args.acceptance
            else "table-review-local",
            "port": port,
            "url": "http://127.0.0.1:" + str(port),
            "initialized_at": datetime.now(UTC).isoformat(),
        }
        env_file.write_text(
            "POSTGRES_PASSWORD="
            + secrets.token_urlsafe(32)
            + "\nTABLE_REVIEW_MODELS="
            + str(root / "models").replace("\\", "/")
            + "\nTABLE_REVIEW_PORT="
            + str(port)
            + "\nTABLE_REVIEW_PUBLIC_ORIGIN="
            + state["url"]
            + "\nTABLE_REVIEW_SECURE_COOKIES=false\n",
            encoding="utf-8",
        )
        project_file.write_text(json.dumps(state), encoding="utf-8")
    state = json.loads(project_file.read_text(encoding="utf-8"))
    if args.acceptance and not re.fullmatch(
        r"table-review-acceptance-[a-f0-9]{32}", state["project"]
    ):
        raise SystemExit("Invalid isolated acceptance project")
    if not args.acceptance and state["project"] != "table-review-local":
        raise SystemExit("Invalid local project")
    if args.model_volume:
        state["model_volume"] = args.model_volume
    elif args.bind_models:
        state.pop("model_volume", None)
    compose = compose_command(state, env_file)
    if args.stop:
        command(compose + ["stop"])
        print("Recorded services stopped; all volumes preserved")
        return
    state_file = cache / (prefix + "-state.json")
    previous = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    volumes = command(
        [
            "docker",
            "volume",
            "ls",
            "--filter",
            "label=com.docker.compose.project=" + state["project"],
            "--format",
            "{{.Name}}",
        ]
    )
    fresh = not bool(volumes.strip())
    expected = json.loads((root / "infra/model-artifacts.json").read_text(encoding="utf-8"))
    import hashlib

    for profile in expected["profiles"]:
        for name, digest in profile["files"].items():
            with (root / "models" / profile["directory"] / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise SystemExit("Preloaded model artifacts differ from recorded identities")
    mark_model = json.loads((root / "infra/ocr-mark-model.json").read_text(encoding="utf-8"))
    mark_path = root / "models" / "rapidocr" / mark_model["name"]
    if not mark_path.is_file():
        raise SystemExit("Preload the pinned OCR recognition models first")
    with mark_path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != mark_model["sha256"]:
            raise SystemExit("Preloaded OCR mark model differs from recorded identity")
    # Deployment is not a preload action: never bless changed bundled OCR files
    # by replacing their frozen manifest during a storage change or rollback.
    verify_source_models(root / "models")
    command(compose + ["build", "api", "worker"], timeout=900)
    if state.get("model_volume"):
        command(["docker", "volume", "inspect", state["model_volume"]])
        # Validate the exact source manifest and all destination bytes before
        # replacing any serving container. This operator check has no DB access.
        lock_hash = hashlib.sha256((root / "models/runtime-manifest.lock").read_bytes()).hexdigest()
        command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "512m",
                "--cpus",
                "2",
                "--pids-limit",
                "64",
                "--user",
                "10001:10001",
                "--tmpfs",
                "/tmp:size=64m,mode=1777",
                "-v",
                state["model_volume"] + ":/models:ro",
                "table-review-worker:local",
                "python",
                "-c",
                (
                    "import hashlib,sys; from pathlib import Path; from table_review.runtime import models_available; "
                    "root=Path('/models'); assert models_available(root); "
                    "assert hashlib.sha256((root/'runtime-manifest.lock').read_bytes()).hexdigest()==sys.argv[1]"
                ),
                lock_hash,
            ],
            timeout=90,
        )
    command(compose + ["stop", "api", "worker"])
    command(compose + ["up", "-d", "db"])
    command(compose + ["run", "--rm", "initialize"])
    command(compose + ["up", "-d", "api", "worker"])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            response = httpx.get(state["url"] + "/api/v1/health/ready", timeout=5)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise SystemExit("Deployment did not become ready within its startup bound")
    worker = json.loads(command(["docker", "inspect", state["project"] + "-worker-1"]))[0]
    state.update(
        worker_network=worker["HostConfig"]["NetworkMode"],
        worker_memory_bytes=worker["HostConfig"]["Memory"],
        worker_uid=int(worker["Config"]["User"].split(":")[0]),
        fresh_deployment=fresh,
        initial_deployment_fresh=previous.get(
            "initial_deployment_fresh", previous.get("fresh_deployment", fresh)
        ),
        verified_at=datetime.now(UTC).isoformat(),
        images={
            service: json.loads(
                command(["docker", "inspect", state["project"] + "-" + service + "-1"])
            )[0]["Image"]
            for service in ("api", "worker", "db")
        },
        adapter_contract_version=command(
            [
                "docker",
                "exec",
                state["project"] + "-worker-1",
                "python",
                "-c",
                "from table_review.extraction.provenance import ADAPTER_CONTRACT_VERSION; print(ADAPTER_CONTRACT_VERSION)",
            ]
        ).strip(),
        model_storage="verified_named_volume"
        if state.get("model_volume")
        else "original_bind_mount",
    )
    recorded_state = {**previous, **state}
    if not state.get("model_volume"):
        recorded_state.pop("model_volume", None)
    state_file.write_text(json.dumps(recorded_state, indent=2), encoding="utf-8")
    recorded = json.loads(project_file.read_text(encoding="utf-8"))
    if state.get("model_volume"):
        recorded["model_volume"] = state["model_volume"]
    else:
        recorded.pop("model_volume", None)
    project_file.write_text(json.dumps(recorded), encoding="utf-8")
    print("Private local application ready at " + state["url"])


if __name__ == "__main__":
    main()
