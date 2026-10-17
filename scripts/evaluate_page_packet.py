"""Run a frozen page packet in the offline worker, one bounded child per page.

Bind the repository read-only, models read-only and a fresh output directory
writable. References and execution identities must be frozen before this runs.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# The bounded child deliberately drops PYTHONPATH. Explicitly select the same
# mounted source and evaluation modules in both parent and child processes.
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from evaluation.manifest import load_manifest, payload_digest
from evaluation.page_manifest import validate_page_bindings, verify_execution_identity
from evaluation.pages import score_page
from table_review.extraction.provenance import runtime_manifest
from table_review.extraction.types import (
    ExtractionRequest,
    ExtractionResult,
    ExtractorConfig,
)
from table_review.jobs.runner import ProcessingFailure, run_child
from table_review.jobs.types import ProcessingLimits
from table_review.runtime import models_available


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def live_identity(models):
    if not models_available(models):
        raise ValueError("Offline model bytes failed verification")
    return runtime_manifest(), hashlib.sha256(
        (models / "runtime-manifest.lock").read_bytes()
    ).hexdigest()


def execute(args):
    corpus = load_manifest(args.manifest)
    plan = json.loads(args.page_plan.read_text(encoding="utf-8"))
    if payload_digest(plan) != plan["frozen_payload_sha256"]:
        raise ValueError("Frozen page plan hash differs")
    if hashlib.sha256(args.manifest.read_bytes()).hexdigest() != plan["corpus_sha256"]:
        raise ValueError("Frozen corpus differs from page plan")
    validate_page_bindings(corpus, plan)
    identity = json.loads(args.execution_identity.read_text(encoding="utf-8-sig"))
    if set(identity["files"]) != {
        "evaluation/manifest.py",
        "evaluation/metrics.py",
        "evaluation/pages.py",
        "evaluation/page_manifest.py",
        "scripts/evaluate_page_packet.py",
    }:
        raise ValueError("Execution identity must pin every evaluator module and runner")
    runtime, model_hash = live_identity(args.models)
    verify_execution_identity(identity, ROOT, runtime, model_hash)
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError("Use an empty output directory; preserve earlier attempts")
    write(
        args.output / "started.json",
        {
            "started_at": datetime.now(UTC).isoformat(),
            "corpus_sha256": plan["corpus_sha256"],
            "page_protocol_sha256": hashlib.sha256(args.page_plan.read_bytes()).hexdigest(),
            "execution_identity": identity,
            "reference_status": "agent_authored_provisional_not_independent",
            "limits": {"timeout_seconds_per_page": args.timeout, "memory_bytes": 6 * 1024**3},
        },
    )
    reports = {report.id: report for report in corpus.reports}
    cases = {case.id: case for case in corpus.cases}
    records = []
    for index, entry in enumerate(plan["pages"]):
        report = reports[entry["report_id"]]
        source = (args.manifest.parent / report.source_path).resolve()
        label = f"page-{index:03d}"
        destination = args.output / (label + ".extraction.json")
        truths = tuple(cases[identity] for identity in entry["case_ids"])
        failure, extracted, metrics = None, None, {}
        started = time.monotonic()
        try:
            if hashlib.sha256(source.read_bytes()).hexdigest() != report.sha256:
                failure = "source_hash_mismatch"
            else:
                metrics = run_child(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--child",
                        "--source",
                        str(source),
                        "--page",
                        str(entry["page"]),
                        "--models",
                        str(args.models),
                        "--output",
                        str(destination),
                    ],
                    ProcessingLimits(timeout_seconds=args.timeout),
                    work_dir=args.output / (label + "-work"),
                )
                extracted = ExtractionResult.model_validate_json(
                    destination.read_text(encoding="utf-8")
                )
        except ProcessingFailure as error:
            failure = error.code
        except OSError:
            failure = "source_or_output_unavailable"
        except (ValueError, KeyError, TypeError):
            failure = "invalid_extraction_output"
        score = score_page(
            truths,
            extracted,
            page=entry["page"],
            failure_code=failure,
            adjudicated=entry["adjudicated"],
        )
        record = {
            "report_id": report.id,
            "purpose": entry["purpose"],
            **score,
            "wall_seconds": time.monotonic() - started,
            "process_metrics": metrics,
            "extraction_file": destination.name if extracted is not None else None,
        }
        records.append(record)
        write(args.output / "page-results.json", records)
        print(
            json.dumps(
                {
                    "page": index + 1,
                    "of": len(plan["pages"]),
                    "tables": score["predicted_tables"],
                    "failure": failure,
                    "seconds": round(record["wall_seconds"], 2),
                }
            ),
            flush=True,
        )
    # Verify that neither scorer/runtime nor model bytes changed during execution.
    runtime, model_hash = live_identity(args.models)
    verify_execution_identity(identity, ROOT, runtime, model_hash)
    write(
        args.output / "completed.json",
        {
            "finished_at": datetime.now(UTC).isoformat(),
            "pages": len(records),
            "failed_pages": sum(row["failed_pages"] for row in records),
            "execution_identity_rechecked": True,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--page", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--page-plan", type=Path)
    parser.add_argument("--execution-identity", type=Path)
    parser.add_argument("--models", type=Path, default=Path("/models"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if args.child:
        from table_review.extraction.docling_adapter import DoclingExtractor

        result = DoclingExtractor().extract(
            ExtractionRequest(
                source_path=args.source,
                pages=(args.page,),
                config=ExtractorConfig(artifacts_path=args.models),
            )
        )
        write(args.output, result.model_dump(mode="json"))
    else:
        if not all((args.manifest, args.page_plan, args.execution_identity)) or args.timeout <= 0:
            parser.error(
                "A frozen manifest, page plan, execution identity and positive timeout are required"
            )
        execute(args)


if __name__ == "__main__":
    main()
