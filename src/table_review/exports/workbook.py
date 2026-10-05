import hashlib
import io
import json
import math
import zipfile
from decimal import Decimal
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from table_review.documents.service import DocumentError
from table_review.exports.types import ExportArtifact, ExportInput, export_manifest
from table_review.tables.grid import validate_grid
from table_review.tables.numbers import interpret_value


def typed_value(cell):
    parsed = interpret_value(cell.text, cell.role, cell.interpretation)
    number = parsed.decimal_value
    if number is None or (number.is_zero() and number.is_signed()):
        return cell.text, "@"
    numeric = float(number)
    if not math.isfinite(numeric) or Decimal(str(numeric)) != number:
        return cell.text, "@"
    source = cell.text.strip("()%").replace(",", "").lstrip("+-")
    places = len(source.split(".", 1)[1]) if "." in source else 0
    format = ("#,##0" if "," in cell.text else "0") + ("." + "0" * places if places else "")
    if parsed.kind == "percent":
        format += "%"
    if cell.text.startswith("("):
        format += f";({format})"
    elif cell.text.startswith("+"):
        format = f'"+"{format};-{format};"+"{format}'
    return numeric, format


def text_cell(sheet, row, column, value):
    value = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, sort_keys=True)
    )
    # Excel cannot encode arbitrary control characters or more than 32767 characters.
    # JSON is reversible for those metadata values; never let openpyxl truncate silently.
    if len(value) > 32767 or ILLEGAL_CHARACTERS_RE.search(value):
        raise DocumentError(
            "workbook_text_unsupported",
            "Use the CSV package to preserve text outside workbook limits",
        )
    target = sheet.cell(row, column)
    target.value = value
    target.data_type, target.number_format = "s", "@"
    target.alignment = Alignment(vertical="center", wrap_text=True)
    return target


def _records(sheet, headers, rows):
    for column, header in enumerate(headers, 1):
        text_cell(sheet, 1, column, header)
    for row, values in enumerate(rows, 2):
        for column, value in enumerate(values, 1):
            text_cell(sheet, row, column, value)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def write_workbook(data: ExportInput, target) -> ExportArtifact:
    validate_grid(data.content)
    wb = Workbook()
    table = wb.active
    table.title = "Table"
    source, changes, manifest_sheet = [
        wb.create_sheet(name) for name in ("Source", "Changes", "Manifest")
    ]
    manifest = export_manifest(data, "workbook")
    for cell in data.content.cells:
        value, format = typed_value(cell)
        target_cell = (
            text_cell(table, cell.row + 1, cell.col + 1, value)
            if format == "@"
            else table.cell(cell.row + 1, cell.col + 1, value)
        )
        target_cell.number_format = format
        target_cell.alignment = Alignment(
            horizontal="left" if format == "@" else "right", vertical="center", wrap_text=True
        )
        if cell.row_span > 1 or cell.col_span > 1:
            table.merge_cells(
                start_row=cell.row + 1,
                start_column=cell.col + 1,
                end_row=cell.row + cell.row_span,
                end_column=cell.col + cell.col_span,
            )
    # Notes/status sit outside the exact table geometry, so A1 coordinates stay reproducible.
    note_column = data.content.columns + 2
    text_cell(
        table,
        1,
        note_column,
        {
            "draft": "Draft",
            "reviewed": "Reviewed",
            "reviewed_with_warnings": "Reviewed with warnings",
        }[manifest["review_status"]],
    )
    text_cell(table, 2, note_column, "Unresolved warnings: " + str(manifest["unresolved_count"]))
    for row, note in enumerate(data.content.unit_notes, 4):
        text_cell(table, row, note_column, note)
    _records(
        source,
        [
            "Cell ID",
            "Row",
            "Column",
            "Span",
            "Raw text",
            "Current text",
            "Role",
            "Interpretation",
            "Source references",
            "Export type",
        ],
        (
            [
                c.id,
                c.row + 1,
                c.col + 1,
                [c.row_span, c.col_span],
                c.raw_text,
                c.text,
                c.role,
                c.interpretation,
                {
                    "primary": c.reference.model_dump(mode="json"),
                    "all": [r.model_dump(mode="json") for r in c.source_references],
                },
                "text" if typed_value(c)[1] == "@" else "number",
            ]
            for c in data.content.cells
        ),
    )
    _records(
        changes,
        [
            "Event",
            "Base revision",
            "Result revision",
            "Actor",
            "Time",
            "Operation",
            "Affected cells",
        ],
        (
            [
                e.get("id"),
                e.get("base_revision_id"),
                e.get("result_revision_id"),
                e.get("actor_id"),
                e.get("created_at"),
                e.get("operation"),
                e.get("affected_cell_ids"),
            ]
            for e in data.edit_events
        ),
    )
    # Bounded JSON chunks preserve long manifests without Excel's silent text truncation.
    _records(
        manifest_sheet,
        ["Field", "Part", "Value (JSON)"],
        (
            [key, index + 1, encoded[start : start + 600]]
            for key, value in manifest.items()
            for encoded in [json.dumps(value, ensure_ascii=True, sort_keys=True)]
            for index, start in enumerate(range(0, len(encoded), 600))
        ),
    )
    for sheet in wb:
        sheet.sheet_view.showGridLines = False
        for row in sheet:
            for cell in row:
                cell.font = Font(name="Arial", size=10)
        for col in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(col)].width = 24 if col > 1 else 40
        for row in range(1, sheet.max_row + 1):
            sheet.row_dimensions[row].height = 32
        for header in tuple(r + 1 for r in data.content.header_rows) if sheet is table else (1,):
            for cell in sheet[header]:
                cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="244A41")
    source.column_dimensions["A"].width = 42
    for column in ("B", "C", "D"):
        source.column_dimensions[column].width = 10
    source.column_dimensions["I"].width = 90
    for column in ("A", "B", "C", "D"):
        changes.column_dimensions[column].width = 42
    for column in ("E", "F", "G"):
        changes.column_dimensions[column].width = 70
    manifest_sheet.column_dimensions["B"].width = 8
    manifest_sheet.column_dimensions["C"].width = 100
    for sheet in wb:
        for row in sheet:
            lines = max(
                (
                    sum(
                        max(
                            1,
                            math.ceil(
                                len(line)
                                / max(
                                    1,
                                    sheet.column_dimensions[get_column_letter(cell.column)].width
                                    - 3,
                                )
                            ),
                        )
                        for line in str(cell.value or "").split("\n")
                    )
                    for cell in row
                ),
                default=1,
            )
            sheet.row_dimensions[row[0].row].height = min(409, max(28, lines * 15 + 8))
    table.column_dimensions[get_column_letter(note_column)].width = 45
    if data.content.header_rows:
        table.freeze_panes = f"{get_column_letter(max(data.content.row_label_columns, default=-1) + 2)}{max(data.content.header_rows) + 2}"
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = io.BytesIO()
    wb.save(raw)
    # Clock metadata must not change an artifact regenerated from the identical snapshot.
    with (
        zipfile.ZipFile(raw) as original,
        zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output,
    ):
        for name in sorted(original.namelist()):
            value = original.read(name)
            if name == "docProps/core.xml":
                root = ET.fromstring(value)
                for tag in ("created", "modified"):
                    element = root.find("{http://purl.org/dc/terms/}" + tag)
                    if element is not None:
                        element.text = "1980-01-01T00:00:00Z"
                value = ET.tostring(root, encoding="utf-8")
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(entry, value)
    return ExportArtifact(
        format="workbook",
        path=target,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        manifest=manifest,
    )
