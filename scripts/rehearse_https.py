"""Rehearse the hosted HTTPS boundary on the isolated acceptance project only.

Never expose a public port, install a trust root, or touch normal saved volumes.
The public root certificate is trusted by this probe's TLS client only.
"""

import argparse
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.deploy_local import command, compose_command


def isolated(state):
    if not re.fullmatch(r"table-review-acceptance-[a-f0-9]{32}", state.get("project", "")):
        raise ValueError("HTTPS rehearsal requires the isolated acceptance project")
    return state


def rehearsal_overrides(port, http_port):
    if (
        any(type(p) is not int or not 1024 <= p <= 65535 for p in (port, http_port))
        or port == http_port
    ):
        raise ValueError("Rehearsal requires two distinct unprivileged ports")
    site = f"localhost:{port}"
    return {
        "TABLE_REVIEW_SITE": site,
        "TABLE_REVIEW_HOSTNAME": "localhost",
        "TABLE_REVIEW_PUBLIC_ORIGIN": "https://" + site,
        "TABLE_REVIEW_SECURE_COOKIES": "true",
        "TABLE_REVIEW_HTTPS_PORT": str(port),
        "TABLE_REVIEW_HTTP_PORT": str(http_port),
        "TABLE_REVIEW_BIND_IP": "127.0.0.1",
        "TABLE_REVIEW_TLS_MODE": "rehearsal_tls",
    }


def frozen_environment(private, overrides):
    # runtime.env is operator-generated KEY=value text, not arbitrary shell code.
    recorded = dict(
        line.split("=", 1) for line in private.splitlines() if line and not line.startswith("#")
    )
    return {**os.environ, **recorded, **overrides}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    cache = root / ".cache"
    path, env_file = cache / "https-rehearsal.json", cache / "https-rehearsal.env"
    if args.stop:
        state = isolated(json.loads(path.read_text(encoding="utf-8")))
        compose = compose_command(state, env_file) + ["-f", "infra/compose.https.yaml"]
        overrides = rehearsal_overrides(
            state["rehearsal_ports"]["https"], state["rehearsal_ports"]["http"]
        )
        command(
            compose + ["stop"],
            env=frozen_environment(env_file.read_text(encoding="utf-8"), overrides),
        )
        state.update(status="stopped", stopped_at=datetime.now(UTC).isoformat())
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print("Isolated HTTPS services stopped; volumes and certificate evidence retained")
        return
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("status") != "stopped":
            raise SystemExit("Stop the recorded HTTPS rehearsal before starting another")
        history = cache / "https-history" / uuid4().hex
        history.mkdir(parents=True)
        path.replace(history / path.name)
        if env_file.exists():
            env_file.replace(history / env_file.name)
    state = isolated(json.loads((cache / "runtime-state.json").read_text(encoding="utf-8")))
    with socket.socket() as https, socket.socket() as plain:
        https.bind(("127.0.0.1", 0))
        plain.bind(("127.0.0.1", 0))
        port, http_port = https.getsockname()[1], plain.getsockname()[1]
    extra = rehearsal_overrides(port, http_port)
    origin = extra["TABLE_REVIEW_PUBLIC_ORIGIN"]
    private = (cache / "runtime.env").read_text(encoding="utf-8").rstrip()
    env_file.write_text(
        private + "\n" + "\n".join(f"{k}={v}" for k, v in extra.items()) + "\n", encoding="utf-8"
    )
    env_file.chmod(0o600)
    compose = compose_command(state, env_file) + ["-f", "infra/compose.https.yaml"]
    environment = frozen_environment(private, extra)
    run = partial(command, env=environment)
    state.update(
        url=origin,
        http_url=f"http://localhost:{http_port}",
        status="starting",
        rehearsal_ports={"https": port, "http": http_port},
    )
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    run(compose + ["build", "proxy"])
    run(compose + ["up", "-d", "api", "worker", "proxy"])
    certificate = cache / (state["project"] + "-root.crt")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        root_ca = subprocess.run(
            [
                "docker",
                "exec",
                state["project"] + "-proxy-1",
                "cat",
                "/data/caddy/pki/authorities/local/root.crt",
            ],
            capture_output=True,
            timeout=10,
            check=False,
            env=environment,
        )
        if root_ca.returncode == 0 and b"BEGIN CERTIFICATE" in root_ca.stdout:
            certificate.write_bytes(root_ca.stdout)
            break
        time.sleep(1)
    else:
        raise SystemExit("HTTPS rehearsal certificate did not become available")
    tls = ssl.create_default_context(cafile=str(certificate))
    with httpx.Client(verify=tls, trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline:
            try:
                ready = client.get(origin + "/api/v1/health/ready")
                if ready.status_code == 200 and ready.json()["ready"]:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise SystemExit("HTTPS rehearsal did not become ready")
        redirected = client.get(state["http_url"] + "/api/v1/health/ready")
        assert redirected.status_code == 308
        assert redirected.headers["location"] == origin + "/api/v1/health/ready"
    boundaries = {}
    for service in ("api", "db", "worker", "proxy"):
        inspected = json.loads(run(["docker", "inspect", state["project"] + "-" + service + "-1"]))[
            0
        ]
        published = inspected["HostConfig"].get("PortBindings") or {}
        if service != "proxy":
            assert not published
        else:
            assert all(p["HostIp"] == "127.0.0.1" for ports in published.values() for p in ports)
            assert inspected["Config"]["User"] == "10001:10001"
            assert inspected["HostConfig"]["ReadonlyRootfs"]
        if service == "worker":
            assert inspected["HostConfig"]["NetworkMode"] == "none"
        boundaries[service] = {"image": inspected["Image"], "published_ports": published}
    state.update(
        status="verified",
        verified_at=datetime.now(UTC).isoformat(),
        ca_path=str(certificate.relative_to(root)),
        certificate_verification=True,
        trust_root_installed=False,
        boundaries=boundaries,
    )
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"status": "verified", "url": origin, "tls_verified": True, "public_ports": False}
        )
    )


if __name__ == "__main__":
    main()
