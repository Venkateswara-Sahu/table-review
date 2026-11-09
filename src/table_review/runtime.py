"""Operator initialization and independent component availability, with no secrets in output."""

import argparse
import hashlib
import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import inspect, text

from table_review.lifecycle.journal import JournalUnavailable, LifecycleJournal
from table_review.settings import Settings

_verified_models = {}
_model_verification_lock = threading.RLock()
_MODEL_READINESS_TTL_SECONDS = 30


def write_model_manifest(root):
    root = Path(root).resolve()
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in {".json", ".safetensors", ".onnx", ".pt", ".bin"}
        and p.name != "runtime-manifest.lock"
        and ".cache" not in p.relative_to(root).parts
    )
    if not files:
        raise ValueError("Preload model artifacts before recording their identities")
    manifest = {}
    for path in files:
        with path.open("rb") as stream:
            manifest[path.relative_to(root).as_posix()] = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()
    (root / "runtime-manifest.lock").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _model_paths(root):
    manifest = json.loads((root / "runtime-manifest.lock").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("Offline model identity is unavailable")
    assets = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".json", ".safetensors", ".onnx", ".pt", ".bin"}
        and ".cache" not in path.relative_to(root).parts
    }
    if set(manifest) != assets:
        raise ValueError("Offline model identity is unavailable")
    paths = []
    for name, expected in manifest.items():
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
        ):
            raise ValueError("Offline model identity is unavailable")
        relative = Path(name)
        original = root / relative
        path = original.resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.is_relative_to(root)
            or not path.is_file()
            or original.is_symlink()
        ):
            raise ValueError("Offline model identity is unavailable")
        paths.append((name, path, expected))
    return paths


def _model_signature(paths):
    result = []
    for name, path, expected in paths:
        stat = path.stat()
        result.append(
            (
                name,
                str(path),
                expected,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
                stat.st_ino,
            )
        )
    return tuple(result)


def verified_model_hashes(root):
    """Rehash frozen model bytes on every extraction; readiness cache is never trusted."""
    root = Path(root).resolve()
    with _model_verification_lock:
        _verified_models.pop(str(root), None)
        try:
            paths = _model_paths(root)
            signature = _model_signature(paths)
            hashes = {}
            for name, path, expected in paths:
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != expected:
                    raise ValueError("Offline model identity is unavailable")
                hashes[name] = actual
            if _model_signature(_model_paths(root)) != signature:
                raise ValueError("Offline model identity changed during verification")
            _verified_models[str(root)] = (signature, time.monotonic())
            return hashes
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("Offline model identity is unavailable") from error


def models_available(root, *, force_refresh=False):
    """Readiness may reuse a successful check for at most 30 seconds."""
    root = Path(root).resolve()
    try:
        with _model_verification_lock:
            signature = _model_signature(_model_paths(root))
            cached = _verified_models.get(str(root))
            if not force_refresh and cached:
                previous, checked_at = cached
                if (
                    previous == signature
                    and 0 <= time.monotonic() - checked_at < _MODEL_READINESS_TTL_SECONDS
                ):
                    return True
            verified_model_hashes(root)
            return True
    except (OSError, ValueError, TypeError):
        _verified_models.pop(str(root), None)
        return False


def worker_available(path, now):
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        at = datetime.fromisoformat(record["at"])
        return at.tzinfo is not None and 0 <= (now - at).total_seconds() <= 45
    except (OSError, ValueError, TypeError, KeyError):
        return False


class WorkerPulse:
    def __init__(self, path, interval=2):
        self.path, self.interval = Path(path), interval
        self.stop = threading.Event()

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"at": datetime.now(UTC).isoformat(), "pid": os.getpid()}), encoding="utf-8"
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.path)

    def _loop(self):
        while not self.stop.wait(self.interval):
            try:
                self._write()
            except OSError:
                # No successful pulse means the readiness endpoint reports down.
                return

    def __enter__(self):
        self._write()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=self.interval + 1)
        self.path.unlink(missing_ok=True)


def readiness(app):
    components = {"database": False, "journal": False, "worker": False, "models": False}
    try:
        with app.state.session_factory() as session:
            components["database"] = session.scalar(text("SELECT 1")) == 1
    except Exception:  # noqa: BLE001, S110 - sanitized availability only
        pass
    try:
        app.state.journal.verify()
        components["journal"] = True
    except (JournalUnavailable, OSError):
        pass
    settings = app.state.settings
    components["worker"] = worker_available(settings.pulse_path, datetime.now(UTC))
    components["models"] = models_available(settings.model_path)
    return {"ready": all(components.values()), "components": components}


def initialize(settings):
    from alembic import command
    from alembic.config import Config
    from cryptography.fernet import Fernet

    from table_review.storage.db import create_session_factory

    factory = create_session_factory(settings.database_url, schema=settings.database_schema)
    try:
        with factory.kw["bind"].begin() as connection:
            if (
                inspect(connection).has_table("documents")
                and not settings.lifecycle_journal_path.exists()
            ):
                raise JournalUnavailable("An existing deployment must preserve its current journal")
            journal = LifecycleJournal(settings.lifecycle_journal_path, create=True)
            journal.verify()
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        if settings.backup_key_path and not settings.backup_key_path.exists():
            settings.backup_key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with settings.backup_key_path.open("xb") as stream:
                stream.write(Fernet.generate_key())
            settings.backup_key_path.chmod(0o600)
    finally:
        factory.kw["bind"].dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("initialize", "model-manifest", "serve"))
    parser.add_argument("--models", type=Path, default=Path("models"))
    args = parser.parse_args()
    if args.action == "model-manifest":
        write_model_manifest(args.models)
        print("Model artifact identities recorded")
        return
    settings = Settings.from_env()
    if args.action == "initialize":
        initialize(settings)
        print("Explicit initialization completed")
    else:
        import uvicorn
        from fastapi import HTTPException
        from fastapi.responses import FileResponse

        from table_review.app import create_app

        app = create_app(settings)
        static = Path(os.environ.get("TABLE_REVIEW_WEB_ROOT", "/app/web/dist"))
        if static.is_dir():
            root = static.resolve()
            index = root / "index.html"

            async def serve_web(full_path: str):
                if full_path == "api" or full_path.startswith("api/"):
                    raise HTTPException(status_code=404)
                target = (root / full_path).resolve()
                if target.is_file() and root in target.parents:
                    return FileResponse(target)
                # Client routes (for example /reports/<id>/pages/4) survive a
                # refresh; the SPA router resolves them in the browser.
                return FileResponse(index)

            app.add_api_route(
                "/{full_path:path}",
                serve_web,
                methods=["GET", "HEAD"],
                include_in_schema=False,
            )
        uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - never print configuration/DB exception text
        raise SystemExit("Runtime operation failed; inspect component readiness") from None
