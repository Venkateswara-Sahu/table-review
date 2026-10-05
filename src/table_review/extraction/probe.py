"""Local, source-hashed development probe. Anchor presence is not full cell accuracy."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from table_review.documents.types import PageBox
from table_review.extraction.docling_adapter import DoclingExtractor
from table_review.extraction.types import ExtractionRequest, ExtractorConfig, ProbeSummary


def _normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def _overlap(a, b):
    if a is None:
        return 0
    intersection = max(0, min(a.right, b.right) - max(a.left, b.left)) * max(
        0, min(a.bottom, b.bottom) - max(a.top, b.top)
    )
    union = (
        (a.right - a.left) * (a.bottom - a.top)
        + (b.right - b.left) * (b.bottom - b.top)
        - intersection
    )
    return intersection / union if union else 0


def run_probe(manifest_path: Path, output_dir: Path) -> ProbeSummary:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    extractor, results, cache = DoclingExtractor(), [], {}
    for config_data in manifest.get("configs", [{}]):
        config = ExtractorConfig(**config_data)
        for case in manifest["cases"]:
            record = {"case_id": case["id"], "config": config.model_dump(mode="json")}
            source = Path(case["source_path"])
            if not source.is_absolute():
                source = manifest_path.parent / source
            try:
                with source.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != case["source_sha256"]:
                    record.update(outcome="failed", error_type="source_hash_mismatch")
                    results.append(record)
                    continue
                region = PageBox(**case["request_region"]) if case.get("request_region") else None
                request = ExtractionRequest(
                    source_path=source, pages=(case["page"],), region=region, config=config
                )
                key = request.model_dump_json()
                reused = key in cache
                if not reused:
                    cache[key] = extractor.extract(request)
                result = cache[key]
                selected = result.tables
                if case.get("expected_region"):
                    expected = PageBox(**case["expected_region"])
                    selected = tuple(
                        sorted(
                            result.tables, key=lambda t: _overlap(t.region, expected), reverse=True
                        )[:1]
                    )
                    if not selected or _overlap(selected[0].region, expected) < 0.2:
                        selected = ()
                literals = [_normalize(c.raw_text) for t in selected for c in t.content.cells]
                anchors = case.get("anchor_literals", [])
                cells = [c for t in selected for c in t.content.cells]
                grounded = 0
                for table in selected:
                    for cell in table.content.cells:
                        box = cell.reference.box
                        if cell.reference.precision != "cell" or not cell.text.strip() or not box:
                            continue
                        if any(
                            _normalize(token.text) in _normalize(cell.raw_text)
                            and box.left <= (token.box.left + token.box.right) / 2 <= box.right
                            and box.top <= (token.box.top + token.box.bottom) / 2 <= box.bottom
                            for token in table.source_tokens
                        ):
                            grounded += 1
                record.update(
                    outcome="succeeded" if selected else "no_table",
                    detected_page_tables=len(result.tables),
                    selected_tables=len(selected),
                    cells=len(cells),
                    anchor_literals_count=len(anchors),
                    anchor_literals_correct=sum(
                        any(_normalize(a) in text for text in literals) for a in anchors
                    ),
                    missing_anchor_literals=[
                        a for a in anchors if not any(_normalize(a) in text for text in literals)
                    ],
                    precise_cell_boxes=sum(c.reference.precision == "cell" for c in cells),
                    nonempty_cells=sum(bool(c.text.strip()) for c in cells),
                    cells_with_matching_token_center=grounded,
                    elapsed_seconds=result.manifest["elapsed_seconds"],
                    peak_rss_bytes=result.manifest["peak_rss_bytes"],
                    reused_page_result=reused,
                    warnings=result.warnings,
                    extraction_manifest=result.manifest,
                )
                label = f"{case['id']}-matching-{str(config.cell_matching).lower()}"
                (output_dir / f"{label}.json").write_text(
                    result.model_dump_json(indent=2), encoding="utf-8"
                )
            except Exception as error:  # noqa: BLE001 - record every case failure at the CLI boundary
                # Local development reports retain diagnostic class/message, not uploaded private data.
                record.update(outcome="failed", error_type=type(error).__name__, error=str(error))
            results.append(record)
    results_path = output_dir / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "metric_scope": "selected development anchors; not full cell accuracy",
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return ProbeSummary(
        case_count=len(results),
        failures=sum(r["outcome"] != "succeeded" for r in results),
        results_path=results_path,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = run_probe(args.manifest, args.output)
    print(summary.model_dump_json())
    raise SystemExit(1 if summary.failures else 0)


if __name__ == "__main__":
    main()
