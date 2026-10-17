"""Sequential offline cold/reused-context diagnostics; no report text in output.

Run inside the bounded worker container with preloaded models and read-only inputs.
Reused-context samples are diagnostic: production still uses fresh report children.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def checkpoint(path, value):
    path = Path(path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_checkpoint(path):
    try:
        if path.stat().st_size > 2 * 1024**2:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("runs"), list)
            or type(value.get("active_context_reused")) not in (bool, type(None))
        ):
            return None
        return value
    except (OSError, ValueError):
        return None


def sample(args):
    runs = []
    checkpoint(args.output, {"runs": runs, "active_context_reused": False})
    from table_review.extraction.docling_adapter import DoclingExtractor
    from table_review.extraction.types import ExtractionRequest, ExtractorConfig

    extractor = DoclingExtractor()
    request = ExtractionRequest(
        source_path=args.source,
        pages=(args.page,),
        config=ExtractorConfig(artifacts_path=args.models),
    )
    for reused in (False, True):
        checkpoint(args.output, {"runs": runs, "active_context_reused": reused})
        started = time.monotonic()
        result = extractor.extract(request)
        canonical = json.dumps(
            [table.model_dump(mode="json") for table in result.tables],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        runs.append(
            {
                "context_reused": reused,
                "seconds": time.monotonic() - started,
                "tables": [
                    {"rows": table.content.rows, "columns": table.content.columns}
                    for table in result.tables
                ],
                "table_evidence_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                "manifest": {
                    key: result.manifest.get(key)
                    for key in (
                        "source_sha256",
                        "model_artifact_hashes",
                        "versions",
                        "adapter_contract_version",
                        "code_revision",
                        "code_revision_kind",
                        "environment",
                        "conversion_outcome",
                        "timing",
                        "peak_rss_bytes",
                        "dependency_lock_sha256",
                    )
                },
            }
        )
        checkpoint(args.output, {"runs": runs, "active_context_reused": None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--models", type=Path, default=Path("/models"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=range(3, 11), default=3)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        sample(args)
        return

    from table_review.jobs.runner import ProcessingFailure, run_child
    from table_review.jobs.types import ProcessingLimits

    pairs = []
    for iteration in range(args.repeats):
        with tempfile.TemporaryDirectory(prefix="extraction-benchmark-") as temporary:
            root = Path(temporary)
            result_path = root / "measurements.json"
            try:
                metrics = run_child(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--child",
                        "--source",
                        str(args.source),
                        "--page",
                        str(args.page),
                        "--models",
                        str(args.models),
                        "--output",
                        str(result_path),
                    ],
                    ProcessingLimits(timeout_seconds=180, memory_bytes=6 * 1024**3),
                    work_dir=root,
                )
                progress = read_checkpoint(result_path)
                if progress is None:
                    raise ProcessingFailure("infrastructure_interruption")
                pairs.append(
                    {
                        "iteration": iteration,
                        "runs": progress["runs"],
                        "bounded_runner": metrics,
                    }
                )
            except ProcessingFailure as error:
                partial = read_checkpoint(result_path)
                pairs.append(
                    {
                        "iteration": iteration,
                        "failure_code": error.code,
                        "runs": partial["runs"] if partial else [],
                        "failed_context_reused": partial["active_context_reused"]
                        if partial
                        else None,
                        "checkpoint_available": partial is not None,
                    }
                )
        # Retain completed/failing observations if the operator interrupts a later sample.
        checkpoint(args.output, {"pairs": pairs})
        print(
            json.dumps({"iteration": iteration, "failed": "failure_code" in pairs[-1]}), flush=True
        )
    print(json.dumps({"pairs": pairs}), flush=True)
    if any("failure_code" in pair for pair in pairs):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
