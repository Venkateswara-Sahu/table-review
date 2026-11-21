"""One-command zero-friction launcher for local Table Review workbenches."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

import httpx

from table_review.runtime import models_available

_VLM_ENV_KEYS = (
    "TABLE_REVIEW_VLM_BASE_URL",
    "TABLE_REVIEW_VLM_MODEL",
    "TABLE_REVIEW_VLM_API_KEY",
    "TABLE_REVIEW_VLM_TIMEOUT_SECONDS",
)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip()
    return values


def ensure_dev_env(env_file: Path) -> dict[str, str]:
    """Load .cache/dev.env or initialize it with a fresh random local Postgres password."""
    values = _read_env_file(env_file)
    if not values.get("POSTGRES_PASSWORD"):
        values["POSTGRES_PASSWORD"] = secrets.token_urlsafe(24)
        env_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{k}={v}" for k, v in sorted(values.items())]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            env_file.chmod(0o600)
        except OSError:
            pass
    return values


def select_default_backend(models_root: Path, requested: str | None) -> str:
    """Use native_fast when local Docling weights are absent unless overridden."""
    if requested is not None:
        return requested
    return "auto" if models_available(models_root) else "native_fast"


def build_runtime_env(
    root: Path,
    *,
    database_url: str,
    port: int,
    origin: str,
    backend: str,
    device: str,
    retention_days: str,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Assemble the subprocess environment for local API, worker, and DB initialization."""
    data_root = root / ".cache" / "local-data"
    env = dict(os.environ if base_env is None else base_env)
    env.update(
        {
            "TABLE_REVIEW_DATABASE_URL": database_url,
            "TABLE_REVIEW_BLOB_ROOT": str(data_root / "blobs"),
            "TABLE_REVIEW_JOURNAL_PATH": str(data_root / "lifecycle" / "journal.sqlite3"),
            "TABLE_REVIEW_BACKUP_KEY_PATH": str(data_root / "keys" / "backup.key"),
            "TABLE_REVIEW_BACKUP_ROOT": str(data_root / "backups"),
            "TABLE_REVIEW_WORKER_PULSE_PATH": str(data_root / "runtime" / "worker-pulse.json"),
            "TABLE_REVIEW_MODEL_PATH": str(root / "models"),
            "TABLE_REVIEW_BIND_HOST": "127.0.0.1",
            "TABLE_REVIEW_BIND_PORT": str(port),
            "TABLE_REVIEW_PUBLIC_ORIGIN": origin,
            "TABLE_REVIEW_SECURE_COOKIES": "false",
            "TABLE_REVIEW_DEPLOYMENT_MODE": "local",
            "TABLE_REVIEW_RETENTION_DAYS": retention_days,
            "TABLE_REVIEW_EXTRACTION_BACKEND": backend,
            "TABLE_REVIEW_EXTRACTION_DEVICE": device,
            "TABLE_REVIEW_WEB_ROOT": str(root / "web" / "dist"),
        }
    )
    if base_env is not None:
        for key in _VLM_ENV_KEYS:
            if key in base_env:
                env[key] = base_env[key]
    return env


def _npm_executable() -> str:
    found = shutil.which("npm.cmd") or shutil.which("npm")
    if not found:
        raise SystemExit("Node.js/npm is required to build or run the frontend UI")
    return found


def _dev_compose(root: Path, env_file: Path, *compose_args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(root / "infra" / "compose.dev.yaml"),
        *compose_args,
    ]


def _start_dev_postgres(root: Path, env_file: Path) -> str:
    if not shutil.which("docker"):
        raise SystemExit(
            "Docker was not found. Start Docker Desktop or set TABLE_REVIEW_DATABASE_URL."
        )
    env_values = ensure_dev_env(env_file)
    up = subprocess.run(
        _dev_compose(root, env_file, "up", "-d", "db"),
        capture_output=True,
        text=True,
        check=False,
    )
    if up.returncode != 0:
        raise SystemExit(
            "Could not start local PostgreSQL container. Ensure Docker is running, "
            "or set TABLE_REVIEW_DATABASE_URL to an existing PostgreSQL instance."
        )
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        ready = subprocess.run(
            _dev_compose(root, env_file, "exec", "-T", "db", "pg_isready", "-U", "table_review"),
            capture_output=True,
            text=True,
            check=False,
        )
        if ready.returncode == 0:
            password = quote(env_values["POSTGRES_PASSWORD"], safe="")
            return f"postgresql+psycopg://table_review:{password}@127.0.0.1:15432/table_review"
        time.sleep(0.5)
    raise SystemExit("Local PostgreSQL did not become ready within 30 seconds")


def _ensure_web_ready(root: Path, *, build_web: bool, dev_ui: bool) -> None:
    web_dir = root / "web"
    if not (web_dir / "node_modules").is_dir():
        raise SystemExit("Frontend dependencies missing; run `cd web && npm ci` first")
    if dev_ui:
        return
    if build_web or not (web_dir / "dist" / "index.html").is_file():
        print("Building frontend assets...")
        result = subprocess.run([_npm_executable(), "run", "build"], cwd=web_dir, check=False)
        if result.returncode != 0:
            raise SystemExit("Frontend build failed; inspect web build output above")


def _terminate_children(children: list[subprocess.Popen[bytes] | subprocess.Popen[str]]) -> None:
    for proc in reversed(children):
        if proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + 5.0
    for proc in reversed(children):
        if proc.poll() is None:
            remaining = max(0.2, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def _wait_for_api(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/api/v1/setup/status", timeout=2.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise SystemExit("Local Table Review server did not respond within 30 seconds")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start Table Review locally (DB, migrations, warm worker, and web UI)."
    )
    parser.add_argument("--port", type=int, default=8000, help="Local API port (default: 8000)")
    parser.add_argument(
        "--backend",
        choices=("auto", "native_fast", "docling", "vlm"),
        default=None,
        help="Default extraction backend (defaults to auto if Docling models exist, else native_fast)",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default="auto",
        help="Compute device for ML extraction (default: auto)",
    )
    parser.add_argument(
        "--retention-days",
        default="none",
        help="Auto-retention window in days, or 'none' to keep local reports forever (default: none)",
    )
    parser.add_argument(
        "--with-docling",
        action="store_true",
        help="Download and verify offline Docling and RapidOCR weights before starting",
    )
    parser.add_argument(
        "--dev-ui",
        action="store_true",
        help="Run Vite dev server on http://127.0.0.1:5173 with hot reload",
    )
    parser.add_argument(
        "--build-web",
        action="store_true",
        help="Rebuild web/dist before launching the static UI",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the workbench URL in the default browser once ready",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the local dev PostgreSQL container while keeping all saved data",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".cache" / "dev.env"

    if args.stop:
        if env_file.is_file() and shutil.which("docker"):
            subprocess.run(_dev_compose(root, env_file, "stop"), check=False)
        print("Local dev PostgreSQL stopped; all data volumes preserved.")
        return 0

    database_url = os.environ.get("TABLE_REVIEW_DATABASE_URL") or _start_dev_postgres(
        root, env_file
    )
    models_root = root / "models"
    if args.with_docling and not models_available(models_root):
        for script in ("scripts/preload_models.py", "scripts/preload_ocr_models.py"):
            step = subprocess.run([sys.executable, str(root / script)], cwd=root, check=False)
            if step.returncode != 0:
                raise SystemExit(f"Model preload failed ({script})")

    _ensure_web_ready(root, build_web=args.build_web, dev_ui=args.dev_ui)
    backend = select_default_backend(models_root, args.backend)
    api_url = f"http://127.0.0.1:{args.port}"
    ui_url = "http://127.0.0.1:5173" if args.dev_ui else api_url
    runtime_env = build_runtime_env(
        root,
        database_url=database_url,
        port=args.port,
        origin=ui_url,
        backend=backend,
        device=args.device,
        retention_days=args.retention_days,
    )

    init_step = subprocess.run(
        [sys.executable, "-m", "table_review.runtime", "initialize"],
        cwd=root,
        env=runtime_env,
        check=False,
    )
    if init_step.returncode != 0:
        raise SystemExit("Runtime initialization failed; check PostgreSQL and local journal state")

    children: list[subprocess.Popen[bytes] | subprocess.Popen[str]] = []
    try:
        children.append(
            subprocess.Popen([sys.executable, "-m", "table_review.jobs.worker"], cwd=root, env=runtime_env)
        )
        children.append(
            subprocess.Popen(
                [sys.executable, "-m", "table_review.runtime", "serve"], cwd=root, env=runtime_env
            )
        )
        if args.dev_ui:
            vite_env = {**runtime_env, "VITE_API_TARGET": api_url}
            children.append(
                subprocess.Popen([_npm_executable(), "run", "dev"], cwd=root / "web", env=vite_env)
            )
        _wait_for_api(api_url)
        print()
        print("  Table Review Workbench Ready")
        print(f"  URL:        {ui_url}")
        print(f"  Backend:    {backend} (device: {args.device})")
        print(f"  Retention:  {'disabled (keep forever)' if args.retention_days.lower() in {'none', '0', 'off', 'forever'} else args.retention_days + ' days'}")
        if backend == "native_fast":
            print("  Tip:        Run with --with-docling to enable OCR & scanned PDF extraction.")
        print("  First run:  Open the URL above in your browser to create your local workspace account.")
        print("  Press Ctrl+C to stop.")
        print()
        if args.open:
            webbrowser.open(ui_url)
        while True:
            for proc in children:
                code = proc.poll()
                if code is not None:
                    raise SystemExit(f"Service process exited unexpectedly (code {code})")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Table Review services...")
        return 0
    finally:
        _terminate_children(children)


if __name__ == "__main__":
    raise SystemExit(main())
