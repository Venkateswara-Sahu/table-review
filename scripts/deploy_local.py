"""Start a private local deployment; never publish or destroy saved volumes."""

import argparse
import json
import re
import secrets
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from table_review.runtime import models_available, write_model_manifest


def command(args, *, timeout=600):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise SystemExit("Local Docker operation failed; inspect sanitized service readiness/logs")
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    import os

    os.chdir(root)
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    prefix = "runtime" if args.acceptance else "local"
    project_file = cache / (prefix + "-project.json")
    env_file = cache / (prefix + ".env")
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
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-p",
        state["project"],
        "-f",
        "infra/compose.yaml",
    ]
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
    write_model_manifest(root / "models")
    if not models_available(root / "models"):
        raise SystemExit("Preload recorded models first")
    command(compose + ["build", "api", "worker"], timeout=900)
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
    )
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("Private local application ready at " + state["url"])


if __name__ == "__main__":
    main()
