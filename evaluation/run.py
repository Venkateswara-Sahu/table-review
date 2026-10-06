"""Frozen comparisons. All inputs stay in denominators, including interrupted work."""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from evaluation.manifest import CorpusManifest, load_manifest
from evaluation.metrics import CaseResult, overlap, summarize_cases
from table_review.checks.engine import CHECK_VERSION, evaluate_checks
from table_review.checks.types import CheckContext
from table_review.documents.pdf import source_tokens
from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.types import ExtractionRequest, ExtractorConfig
from table_review.jobs.runner import ProcessingFailure, code_identity, run_child
from table_review.jobs.types import ProcessingLimits


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def select_table(truth, extracted):
    candidates = [table for table in extracted.tables if table.page_no == truth.page]
    selected = max(candidates, key=lambda table: overlap(truth.region, table.region), default=None)
    # This is matched-region detection, not all-page precision or open-world recall.
    return selected if selected and overlap(truth.region, selected.region) >= 0.2 else None


def _source_status(report, base):
    source = (base / report.source_path).resolve()
    try:
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        return source, "source_unavailable"
    return source, None if digest == report.sha256 else "source_hash_mismatch"


def evaluate_group(manifest, output, split, mode, cell_matching, *, extractor=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    base = manifest.manifest_path.parent if manifest.manifest_path else Path.cwd()
    reports = {r.id: r for r in manifest.reports}
    sources = {
        id: _source_status(report, base) for id, report in reports.items() if report.split == split
    }
    records = []
    for truth in (case for case in manifest.cases if case.split == split):
        started = time.monotonic()
        _write(
            output / "progress.json",
            {
                "case_id": truth.id,
                "completed": len(records),
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        source, failure = sources[truth.report_id]
        kwargs = {"case_id": truth.id, "truth": truth, "mode": mode, "cell_matching": cell_matching}
        if failure:
            record = CaseResult(**kwargs, table_detected=False, failure_code=failure)
        else:
            try:
                if extractor is None:
                    extractor = DoclingExtractor()
                request = ExtractionRequest(
                    source_path=source,
                    pages=(truth.page,),
                    region=truth.region if mode == "crop" else None,
                    config=ExtractorConfig(
                        cell_matching=cell_matching, artifacts_path=Path("models").resolve()
                    ),
                )
                extracted = extractor.extract(request)
                _write(output / (truth.id + ".extraction.json"), extracted.model_dump(mode="json"))
                selected = select_table(truth, extracted)
                issues = (
                    evaluate_checks(
                        selected.content,
                        CheckContext(
                            source_tokens=selected.source_tokens,
                            source_regions=(selected.region,) if selected.region else (),
                            source_available=bool(selected.source_tokens),
                        ),
                    )
                    if selected
                    else ()
                )
                positions = (
                    tuple(
                        sorted(
                            {
                                (cell.row, cell.col)
                                for cell in selected.content.cells
                                if any(cell.id in issue.affected_cell_ids for issue in issues)
                            }
                        )
                    )
                    if selected
                    else ()
                )
                record = CaseResult(
                    **kwargs,
                    content=selected.content if selected else None,
                    table_detected=selected is not None,
                    elapsed_seconds=time.monotonic() - started,
                    peak_rss_bytes=extracted.manifest["peak_rss_bytes"],
                    issue_count=len(issues),
                    issue_affected_positions=positions,
                )
                _write(
                    output / (truth.id + ".issues.json"),
                    [issue.model_dump(mode="json") for issue in issues],
                )
            except Exception as error:  # noqa: BLE001 - failure-inclusive CLI boundary
                record = CaseResult(
                    **kwargs,
                    table_detected=False,
                    failure_code="extractor_error",
                    elapsed_seconds=time.monotonic() - started,
                )
                _write(
                    output / (truth.id + ".failure.json"), {"exception_type": type(error).__name__}
                )
        records.append(record)
        _write(output / "results.json", [r.model_dump(mode="json") for r in records])
    _write(output / "summary.json", summarize_cases(tuple(records)).model_dump(mode="json"))
    return tuple(records)


def injected_checks(manifest, split):
    """Known single mutations on independently annotated tables; no parser rerun.

    Baseline warnings on the target make that mutation inconclusive. A newly
    attributable coverage/placement issue counts as detection, not correction.
    """
    base = manifest.manifest_path.parent
    reports = {r.id: r for r in manifest.reports}
    records = []
    for truth in (case for case in manifest.cases if case.split == split):
        source, failure = _source_status(reports[truth.report_id], base)
        if failure:
            records.append({"case_id": truth.id, "kind": "unavailable", "failure_code": failure})
            continue
        context = CheckContext(
            source_tokens=source_tokens(source, truth.page), source_regions=(truth.region,)
        )
        content = truth.content
        baseline = evaluate_checks(content, context)
        values = [
            c
            for c in content.cells
            if c.row not in content.header_rows
            and c.col not in content.row_label_columns
            and any(ch.isdigit() for ch in c.text)
            and c.role in ("data", "value")
        ]
        headers = [
            c for c in content.cells if c.role == "column_header" and c.col_span == 1 and c.text
        ]
        mutations = []
        if values:
            target = values[0]
            index = next(i for i, ch in enumerate(target.text) if ch.isdigit())
            value = (
                target.text[:index]
                + ("8" if target.text[index] == "9" else "9")
                + target.text[index + 1 :]
            )
            mutations.append(
                (
                    "digit",
                    (target.id,),
                    content.model_copy(
                        update={
                            "cells": tuple(
                                c.model_copy(update={"text": value}) if c.id == target.id else c
                                for c in content.cells
                            )
                        }
                    ),
                )
            )
        negative = next(
            (
                c
                for c in values
                if "(" in c.text and ")" in c.text or c.text.lstrip().startswith(("-", "−"))
            ),
            None,
        )
        if negative:
            value = negative.text.replace("(", "").replace(")", "").lstrip("-−")
            mutations.append(
                (
                    "sign",
                    (negative.id,),
                    content.model_copy(
                        update={
                            "cells": tuple(
                                c.model_copy(update={"text": value}) if c.id == negative.id else c
                                for c in content.cells
                            )
                        }
                    ),
                )
            )
        if len(headers) >= 2:
            first, second = headers[:2]
            replacements = {first.id: second.text, second.id: first.text}
            mutations.append(
                (
                    "header_swap",
                    (first.id, second.id),
                    content.model_copy(
                        update={
                            "cells": tuple(
                                c.model_copy(update={"text": replacements[c.id]})
                                if c.id in replacements
                                else c
                                for c in content.cells
                            )
                        }
                    ),
                )
            )
        if content.unit_notes:
            mutations.append(
                (
                    "unit",
                    (),
                    content.model_copy(update={"unit_notes": ("INTENTIONALLY WRONG SCALE",)}),
                )
            )
        for kind, ids, changed in mutations:
            existing = {
                (issue.kind, tuple(issue.affected_cell_ids))
                for issue in baseline
                if any(id in issue.affected_cell_ids for id in ids)
            }
            issues = evaluate_checks(changed, context)
            # Revision fingerprints intentionally include unit notes. A new
            # fingerprint alone cannot prove that the checker detected scale.
            baseline_semantics = {
                json.dumps(
                    issue.model_dump(mode="json", exclude={"id", "fingerprint"}),
                    sort_keys=True,
                )
                for issue in baseline
            }
            targeted = (
                [issue for issue in issues if any(id in issue.affected_cell_ids for id in ids)]
                if ids
                else [
                    issue
                    for issue in issues
                    if json.dumps(
                        issue.model_dump(mode="json", exclude={"id", "fingerprint"}),
                        sort_keys=True,
                    )
                    not in baseline_semantics
                ]
            )
            detected = any(
                (issue.kind, tuple(issue.affected_cell_ids)) not in existing for issue in targeted
            )
            records.append(
                {
                    "case_id": truth.id,
                    "kind": kind,
                    "provenance": "injected",
                    "target_ids": ids,
                    "baseline_target_warning": bool(existing),
                    "detected_new_target_issue": detected,
                    "inconclusive": bool(existing),
                    "baseline_issue_count": len(baseline),
                    "changed_issue_count": len(issues),
                }
            )
    return records


def evaluate(manifest: CorpusManifest, output: Path, split: str):
    if split not in ("development", "heldout") or not manifest.manifest_path:
        raise ValueError("Evaluation requires a saved frozen manifest and an explicit split")
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new result directory; do not overwrite frozen comparisons")
    output.mkdir(parents=True)
    started = datetime.now(UTC)
    evaluation_code = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in Path(__file__).parent.glob("*.py")
    }
    groups = {}
    default_summary = None
    for mode in ("full_page", "crop"):
        for matching in (True, False):
            label = mode + ("-matching-on" if matching else "-matching-off")
            destination = output / label
            metrics, failure = {}, None
            group_started = time.monotonic()
            try:
                metrics = run_child(
                    [
                        sys.executable,
                        "-m",
                        "evaluation.run",
                        "--manifest",
                        str(manifest.manifest_path),
                        "--split",
                        split,
                        "--output",
                        str(destination),
                        "--single-group",
                        "--mode",
                        mode,
                        "--matching",
                        "on" if matching else "off",
                    ],
                    ProcessingLimits(),
                    work_dir=output / (label + "-attempt"),
                )
            except ProcessingFailure as error:
                failure = error.code
                metrics = {
                    "elapsed_seconds": time.monotonic() - group_started,
                    "sampled_peak_rss_bytes": None,
                    "hard_memory_enforcement": False,
                    "peak_status": "unavailable_after_interruption",
                }
            path = destination / "results.json"
            results = (
                [
                    CaseResult.model_validate(row)
                    for row in json.loads(path.read_text(encoding="utf-8"))
                ]
                if path.is_file()
                else []
            )
            seen = {r.case_id for r in results}
            for truth in (c for c in manifest.cases if c.split == split and c.id not in seen):
                results.append(
                    CaseResult(
                        case_id=truth.id,
                        truth=truth,
                        table_detected=False,
                        failure_code=failure or "infrastructure_interruption",
                        mode=mode,
                        cell_matching=matching,
                    )
                )
            summary = summarize_cases(tuple(results))
            _write(destination / "results.json", [r.model_dump(mode="json") for r in results])
            _write(destination / "summary.json", summary.model_dump(mode="json"))
            groups[label] = {
                "summary": summary.model_dump(mode="json"),
                "process_metrics": metrics,
                "process_failure": failure,
            }
            if mode == "full_page" and matching:
                default_summary = summary
            print(
                json.dumps(
                    {
                        "completed_group": label,
                        "cases": summary.case_count,
                        "failed": summary.failure_count,
                    }
                ),
                flush=True,
            )
    _write(output / "injected-checks.json", injected_checks(manifest, split))
    _write(
        output / "comparison.json",
        {
            "manifest_sha256": hashlib.sha256(manifest.manifest_path.read_bytes()).hexdigest(),
            "frozen_payload_sha256": manifest.frozen_payload_sha256,
            "split": split,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "check_version": CHECK_VERSION,
            "evaluation_code_sha256": evaluation_code,
            **code_identity(),
            "groups": groups,
            "limits": {
                "group_timeout_seconds": 600,
                "sampled_rss_limit_bytes": 6 * 1024**3,
                "hard_memory_enforcement": False,
            },
            "metric_scope": "matched annotated regions; slot metrics are strict physical cells; unordered occurrences do not establish associations; exact table excludes geometry, which is reported separately",
            "cost_scope": "local CPU, zero model/API requests; energy and monetary hardware costs unmeasured",
        },
    )
    return default_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "heldout"), required=True)
    parser.add_argument("--single-group", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("full_page", "crop"), default="full_page")
    parser.add_argument("--matching", choices=("on", "off"), default="on")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest.resolve())
    if args.single_group:
        evaluate_group(manifest, args.output, args.split, args.mode, args.matching == "on")
    else:
        summary = evaluate(manifest, args.output, args.split)
        print(summary.model_dump_json())


if __name__ == "__main__":
    main()
