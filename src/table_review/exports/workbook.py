import hashlib
import io
import json
import math
import re
import textwrap
import zipfile
from decimal import Decimal

from lxml import etree as ET
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from table_review.documents.service import DocumentError
from table_review.exports.context import context_package, context_records
from table_review.exports.types import (
    ExportArtifact,
    ExportInput,
    cell_review_metadata,
    export_manifest,
)
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
    sheet.auto_filter.ref = sheet.dimensions


def _display_number(text):
    # Alignment only: unsupported currency literals remain exact, inert text.
    return bool(re.fullmatch(r"[\s$£€()\d,.+%−–—-]+", text))


def _wrapped_lines(text, width):
    return sum(
        max(1, len(textwrap.wrap(line, width=max(1, int(width))))) for line in str(text).split("\n")
    )


def _style_table(sheet, content, note_column):
    labels = set(content.row_label_columns or (0,))
    headers = set(content.header_rows)
    for column in range(content.columns):
        body = [
            cell.text
            for cell in content.cells
            if cell.col == column and cell.row not in headers and cell.text
        ]
        if column in labels:
            width = min(56, max(42, max(map(len, body), default=0) * 0.7))
        elif body and sum(_display_number(text) for text in body) >= len(body) * 0.6:
            width = min(20, max(16, max(map(len, body)) + 2))
        else:
            width = 24
        sheet.column_dimensions[get_column_letter(column + 1)].width = width
    rule = Side(style="hair", color="DCE3E9")
    section_rule = Side(style="thin", color="9FAFBE")
    balance_rows = {
        cell.row
        for cell in content.cells
        if cell.col in labels
        and (
            cell.role == "section"
            or re.match(r"^(?:total\b|balance as of\b)", cell.text, re.IGNORECASE)
        )
    }
    for row in range(content.rows):
        sheet.row_dimensions[row + 1].height = 20
        for col in range(content.columns):
            target = sheet.cell(row + 1, col + 1)
            target.font = Font(name="Arial", size=10, color="233342")
            target.border = Border(bottom=rule)
            if row in headers:
                target.font = Font(name="Arial", size=10, bold=True, color="233342")
                target.fill = PatternFill("solid", fgColor="EAF0F5")
                target.border = Border(top=section_rule, bottom=section_rule)
            elif row in balance_rows:
                target.font = Font(name="Arial", size=10, bold=True, color="233342")
                target.fill = PatternFill("solid", fgColor="F5F7F9")
                target.border = Border(top=section_rule, bottom=rule)
    for cell in content.cells:
        target = sheet.cell(cell.row + 1, cell.col + 1)
        header = cell.row in headers or cell.role in ("column_header", "header")
        target.alignment = Alignment(
            horizontal="center"
            if header and cell.col not in labels
            else ("right" if cell.col not in labels and _display_number(cell.text) else "left"),
            vertical="center",
            wrap_text=True,
            indent=1,
        )
        width = sum(
            sheet.column_dimensions[get_column_letter(c + 1)].width
            for c in range(cell.col, cell.col + cell.col_span)
        )
        height = max(20, (_wrapped_lines(cell.text, width - 3) * 12 + 7) / cell.row_span)
        for row in range(cell.row + 1, cell.row + cell.row_span + 1):
            sheet.row_dimensions[row].height = min(
                409, max(sheet.row_dimensions[row].height, height)
            )
    sheet.column_dimensions[get_column_letter(note_column)].width = 48
    for row in sheet.iter_rows(min_col=note_column, max_col=note_column):
        cell = row[0]
        if cell.value is not None:
            cell.font = Font(name="Arial", size=10, color="607485", italic=True)
            cell.alignment = Alignment(vertical="center", wrap_text=False)
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 90
    sheet.sheet_view.selection[0].activeCell = "A1"
    sheet.sheet_view.selection[0].sqref = "A1"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.scale = 100
    sheet.print_options.horizontalCentered = True
    sheet.print_area = (
        f"A1:{get_column_letter(note_column)}{max(content.rows, len(content.unit_notes) + 3, 2)}"
    )


def write_workbook(data: ExportInput, target) -> ExportArtifact:
    validate_grid(data.content)
    wb = Workbook()
    table = wb.active
    table.title = "Table"
    source, changes, manifest_sheet = [
        wb.create_sheet(name) for name in ("Source", "Changes", "Manifest")
    ]
    manifest = export_manifest(data, "workbook")
    context_sheet = wb.create_sheet("Context")
    timeline_sheet = wb.create_sheet("Timeline")
    subtotal_sheets = []
    if data.content.subtotal_groups:
        sheet = wb.create_sheet("Subtotals")
        subtotal_sheets.append(sheet)
        _records(
            sheet,
            ["Group", "Part", "Value (JSON)"],
            (
                [result["group"]["id"], part + 1, encoded[start : start + 600]]
                for result in data.subtotal_results
                for encoded in [json.dumps(result, ensure_ascii=True, sort_keys=True)]
                for part, start in enumerate(range(0, len(encoded), 600))
            ),
        )
    _records(
        timeline_sheet,
        ["Event", "Part", "Value (JSON)"],
        (
            [event.get("id"), index + 1, encoded[start : start + 600]]
            for event in data.timeline_events
            for encoded in [json.dumps(event, ensure_ascii=True, sort_keys=True)]
            for index, start in enumerate(range(0, len(encoded), 600))
        ),
    )
    _records(
        context_sheet,
        ["Evidence", "Part", "Value (JSON)"],
        (
            [identity, part + 1, encoded[start : start + 600]]
            for identity, value in context_records(context_package(data.content))
            for encoded in [json.dumps(value, ensure_ascii=True, sort_keys=True)]
            for part, start in enumerate(range(0, len(encoded), 600))
        ),
    )
    for cell in data.content.cells:
        review = cell_review_metadata(data, cell)
        value, format = (cell.text, "@") if review["verdict"] == "unreadable" else typed_value(cell)
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
            "Cell review",
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
                "text"
                if cell_review_metadata(data, c)["verdict"] == "unreadable"
                or typed_value(c)[1] == "@"
                else "number",
                cell_review_metadata(data, c),
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
    for sheet in (source, changes, manifest_sheet, context_sheet, timeline_sheet, *subtotal_sheets):
        sheet.sheet_view.showGridLines = False
        for row in sheet:
            for cell in row:
                cell.font = Font(name="Arial", size=10)
        for col in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(col)].width = 24 if col > 1 else 40
        for row in range(1, sheet.max_row + 1):
            sheet.row_dimensions[row].height = 32
        for header in (1,):
            for cell in sheet[header]:
                cell.font = Font(name="Arial", size=10, bold=True, color="233342")
                cell.fill = PatternFill("solid", fgColor="EAF0F5")
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
    for sheet in (source, changes, manifest_sheet):
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
    _style_table(table, data.content, note_column)
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
                # Preserve namespace bindings used in QName-valued attributes,
                # including xsi:type="dcterms:W3CDTF" on both timestamps.
                # ElementTree rewrites tag prefixes but leaves attribute values
                # unchanged, producing core properties Excel must repair.
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
