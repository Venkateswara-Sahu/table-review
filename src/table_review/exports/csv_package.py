import csv
import hashlib
import io
import json
import unicodedata
import zipfile

from table_review.exports.context import context_package
from table_review.exports.types import (
    ExportArtifact,
    ExportInput,
    cell_review_metadata,
    export_manifest,
)
from table_review.exports.workbook import typed_value
from table_review.tables.grid import validate_grid


def dangerous_text(text):
    # Normalize only the detection copy. Preserve the authored literal in the package.
    text = unicodedata.normalize("NFKC", text)
    text = text.lstrip("".join(chr(i) for i in range(33)) + "\u00a0\ufeff\u200b")
    return text.startswith(("=", "+", "-", "@"))


def write_csv_package(data: ExportInput, target) -> ExportArtifact:
    validate_grid(data.content)
    rows = [[""] * data.content.columns for _ in range(data.content.rows)]
    cells = []
    for cell in data.content.cells:
        review = cell_review_metadata(data, cell)
        value, format = (cell.text, "@") if review["verdict"] == "unreadable" else typed_value(cell)
        prefix = (
            "[unreadable] "
            if review["verdict"] == "unreadable"
            else "[text] "
            if format == "@" and dangerous_text(cell.text)
            else ""
        )
        rows[cell.row][cell.col] = prefix + cell.text
        cells.append(
            {
                **cell.model_dump(mode="json"),
                "cell_review": review,
                "csv_escaped": bool(prefix),
                "csv_prefix": prefix,
                "export_type": "text" if format == "@" else "number",
                "numeric_value": str(value) if format != "@" else None,
            }
        )
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(rows)
    manifest = export_manifest(data, "csv_package")
    members = {
        "timeline.json": json.dumps(
            {
                "schema": "ordered_review_timeline_v1",
                "through_sequence": data.timeline_through_sequence,
                "events": data.timeline_events,
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode(),
        "context.json": json.dumps(
            context_package(data.content), ensure_ascii=True, sort_keys=True
        ).encode(),
        "table.csv": output.getvalue().encode("utf-8"),
        "cells.json": json.dumps(cells, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        "changes.json": json.dumps(data.edit_events, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        ),
    }
    if data.content.subtotal_groups:
        members["subtotals.json"] = json.dumps(
            {
                "policy": "declared_sum_v1",
                "definitions": [
                    group.model_dump(mode="json") for group in data.content.subtotal_groups
                ],
                "results": data.subtotal_results,
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode()
    manifest["member_sha256"] = {
        name: hashlib.sha256(value).hexdigest() for name, value in members.items()
    }
    members["manifest.json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    return ExportArtifact(
        format="csv_package",
        path=target,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        manifest=manifest,
    )
