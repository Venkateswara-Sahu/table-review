import csv
import hashlib
import io
import json
import unicodedata
import zipfile

from table_review.exports.types import ExportArtifact, ExportInput, export_manifest
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
        value, format = typed_value(cell)
        escaped = format == "@" and dangerous_text(cell.text)
        rows[cell.row][cell.col] = ("[text] " if escaped else "") + cell.text
        cells.append(
            {
                **cell.model_dump(mode="json"),
                "csv_escaped": escaped,
                "export_type": "text" if format == "@" else "number",
                "numeric_value": str(value) if format != "@" else None,
            }
        )
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(rows)
    manifest = export_manifest(data, "csv_package")
    members = {
        "table.csv": output.getvalue().encode("utf-8"),
        "cells.json": json.dumps(cells, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        "changes.json": json.dumps(data.edit_events, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        ),
    }
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
