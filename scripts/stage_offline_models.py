"""Verify and copy preloaded model bytes into a new local model store.

No downloads, replacements or source changes. Mount the source read-only and the
destination writable only during this operator step; runtime mounts stay read-only.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage(source, destination):
    from table_review.runtime import models_available

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Model source and destination must be separate stores")
    if not destination.is_dir() or not models_available(source):
        raise ValueError("Offline source model identity is unavailable")
    lock = "runtime-manifest.lock"
    manifest = json.loads((source / lock).read_text())
    expected = {**manifest, lock: digest(source / lock)}
    for name, checksum in expected.items():
        original, target = source / name, destination / name
        if (
            original.is_symlink()
            or not original.resolve().is_relative_to(source)
            or target.is_symlink()
            or not target.resolve().is_relative_to(destination)
        ):
            raise ValueError("Model paths must remain inside their stores")
        if target.exists() and (not target.is_file() or digest(target) != checksum):
            raise ValueError("Destination model identity conflict; no replacements permitted")
    if any(
        path.is_file() and path.relative_to(destination).as_posix() not in expected
        for path in destination.rglob("*")
    ):
        raise ValueError("Destination contains unexpected files")
    for name in expected:
        original, target = source / name, destination / name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with original.open("rb") as incoming, target.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    if not models_available(destination) or digest(destination / lock) != expected[lock]:
        raise ValueError("Copied model identity verification failed")
    return {
        "verified": True,
        "model_count": len(manifest),
        "bytes": sum((destination / name).stat().st_size for name in manifest),
        "manifest_sha256": expected[lock],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.source, args.destination), sort_keys=True))


if __name__ == "__main__":
    main()
