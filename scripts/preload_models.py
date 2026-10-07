"""Fetch only the recorded public model files and verify their original hashes."""

import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download

from table_review.runtime import write_model_manifest

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "infra/model-artifacts.json").read_text(encoding="utf-8"))
for profile in manifest["profiles"]:
    destination = root / "models" / profile["directory"]
    snapshot_download(
        repo_id=profile["repo_id"],
        revision=profile["revision"],
        local_dir=destination,
        allow_patterns=list(profile["files"]),
        token=False,
    )
    for name, expected in profile["files"].items():
        with (destination / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise SystemExit("Recorded model artifact hash differs")
write_model_manifest(root / "models")
print("Recorded public model artifacts verified")
