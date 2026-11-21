import hashlib
import io
import json
import math
import re
import textwrap
import zipfile
from decimal import Decimal
from pathlib import Path

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
    ReportExportInput,
    cell_review_metadata,
    export_manifest,
)
from table_review.tables.grid import validate_grid
from table_review.tables.numbers import interpret_value

_DASH_ZERO_TEXT = frozenset({"-", "–", "—", "−", "--"})
_STRIP_CURRENCY_RE = re.compile(
    r"(?:US\$|CA\$|A\$|HK\$|S\$|NZ\$|Rs\.?|USD|EUR|GBP|INR|CAD|AUD|JPY|CHF|CNY|[$€£₹¥])",
    re.IGNORECASE,
)
_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def typed_value(cell):
    parsed = interpret_value(cell.text, cell.role, cell.interpretation)
    number = parsed.decimal_value
    if number is None or (number.is_zero() and number.is_signed()):
        return cell.text, "@"
    numeric = float(number)
    if not math.isfinite(numeric) or Decimal(str(numeric)) != number:
        return cell.text, "@"

    stripped = cell.text.strip(" \t\u00a0")
    cleaned = _STRIP_CURRENCY_RE.sub("", stripped).strip("()% \t\u00a0").lstrip("+-−")
    if cell.interpretation == "dash_zero" and stripped in _DASH_ZERO_TEXT:
        places = 0
        has_commas = False
    elif cell.interpretation == "eu_number":
        has_commas = "." in cleaned or " " in cleaned or "\u00a0" in cleaned
        places = len(cleaned.split(",", 1)[1]) if "," in cleaned else 0
    elif cell.interpretation == "percent":
        if "," in cleaned and "." not in cleaned:
            has_commas = False
            places = len(cleaned.split(",", 1)[1])
        else:
            has_commas = "," in cleaned
            places = len(cleaned.split(".", 1)[1]) if "." in cleaned else 0
    else:
        has_commas = "," in cleaned
        places = len(cleaned.split(".", 1)[1]) if "." in cleaned else 0

    use_thousands = has_commas or parsed.currency_symbol in {"$", "€", "£", "₹", "¥"}
    fmt = ("#,##0" if use_thousands else "0") + ("." + "0" * places if places else "")
    if parsed.kind == "percent":
        fmt += "%"
    elif parsed.currency_symbol in {"$", "€", "£", "₹", "¥"}:
        prefix = "$" if parsed.currency_symbol == "$" else f'"{parsed.currency_symbol}"'
        fmt = f"{prefix}{fmt}"

    if "(" in stripped and ")" in stripped:
        fmt = f"{fmt};({fmt})"
    elif stripped.startswith("+"):
        fmt = f'"+"{fmt};-{fmt};"+"{fmt}'
    return numeric, fmt


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
    return bool(re.fullmatch(r"[\s$£€₹¥()\d,.+%−–—-]+", text))


def _wrapped_lines(text, width):
    return sum(
        max(1, len(textwrap.wrap(line, width=max(1, int(width))))) for line in str(text).split("\n")
    )


def _safe_sheet_title(title: str | None, fallback: str, used: set[str]) -> str:
    raw = _INVALID_SHEET_CHARS.sub(" ", (title or "").strip())
    base = " ".join(raw.split()) or fallback
    candidate = base[:31].rstrip() or fallback[:31]
    if candidate not in used:
        used.add(candidate)
        return candidate
    suffix_idx = 2
    while True:
        suffix = f" ({suffix_idx})"
        trimmed = base[: 31 - len(suffix)].rstrip() or fallback[: 31 - len(suffix)]
        option = f"{trimmed}{suffix}"
        if option not in used:
            used.add(option)
            return option
        suffix_idx += 1


def _build_excel_formula(cells_by_id, terms, sheet_prefix: str = "") -> str | None:
    if not terms:
        return None
    resolved = []
    for cell_id, sign in terms:
        cell = cells_by_id.get(cell_id)
        if cell is None:
            return None
        resolved.append((cell, 1 if sign >= 0 else -1))

    if all(sign == 1 for _, sign in resolved) and len(resolved) >= 2:
        cols = {cell.col for cell, _ in resolved}
        rows = {cell.row for cell, _ in resolved}
        if len(cols) == 1:
            sorted_rows = sorted(cell.row for cell, _ in resolved)
            if sorted_rows == list(range(sorted_rows[0], sorted_rows[-1] + 1)):
                col_ltr = get_column_letter(next(iter(cols)) + 1)
                return f"=SUM({sheet_prefix}{col_ltr}{sorted_rows[0] + 1}:{col_ltr}{sorted_rows[-1] + 1})"
        if len(rows) == 1:
            sorted_cols = sorted(cell.col for cell, _ in resolved)
            if sorted_cols == list(range(sorted_cols[0], sorted_cols[-1] + 1)):
                row_num = next(iter(rows)) + 1
                start_ltr = get_column_letter(sorted_cols[0] + 1)
                end_ltr = get_column_letter(sorted_cols[-1] + 1)
                return f"=SUM({sheet_prefix}{start_ltr}{row_num}:{end_ltr}{row_num})"

    parts = []
    for index, (cell, sign) in enumerate(resolved):
        coord = f"{sheet_prefix}{get_column_letter(cell.col + 1)}{cell.row + 1}"
        if index == 0:
            parts.append(coord if sign == 1 else f"-{coord}")
        else:
            parts.append(f"+{coord}" if sign == 1 else f"-{coord}")
    return "=" + "".join(parts)


def _style_table(sheet, content, note_column, last_note_row):
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
        f"A1:{get_column_letter(note_column)}{max(content.rows, last_note_row, 2)}"
    )


def _populate_table_sheet(sheet, data: ExportInput, manifest: dict, *, include_formulas: bool):
    cells_by_id = {cell.id: cell for cell in data.content.cells}
    unreadable_ids = set()
    for cell in data.content.cells:
        review = cell_review_metadata(data, cell)
        if review["verdict"] == "unreadable":
            unreadable_ids.add(cell.id)
        value, fmt = (cell.text, "@") if review["verdict"] == "unreadable" else typed_value(cell)
        target_cell = (
            text_cell(sheet, cell.row + 1, cell.col + 1, value)
            if fmt == "@"
            else sheet.cell(cell.row + 1, cell.col + 1, value)
        )
        target_cell.number_format = fmt
        target_cell.alignment = Alignment(
            horizontal="left" if fmt == "@" else "right", vertical="center", wrap_text=True
        )
        if cell.row_span > 1 or cell.col_span > 1:
            sheet.merge_cells(
                start_row=cell.row + 1,
                start_column=cell.col + 1,
                end_row=cell.row + cell.row_span,
                end_column=cell.col + cell.col_span,
            )

    # Notes/status sit outside the exact table geometry, so A1 coordinates stay reproducible.
    note_column = data.content.columns + 2
    text_cell(
        sheet,
        1,
        note_column,
        {
            "draft": "Draft",
            "reviewed": "Reviewed",
            "reviewed_with_warnings": "Reviewed with warnings",
        }[manifest["review_status"]],
    )
    text_cell(sheet, 2, note_column, "Unresolved warnings: " + str(manifest["unresolved_count"]))
    last_note_row = 3
    for row, note in enumerate(data.content.unit_notes, 4):
        text_cell(sheet, row, note_column, note)
        last_note_row = row
    if data.content.title:
        last_note_row += 1
        text_cell(sheet, last_note_row, note_column, f"Title: {data.content.title}")
    if data.content.table_context:
        parts = [
            f"{label}: {val}"
            for label, val in (
                ("Period", data.content.table_context.period),
                ("Currency", data.content.table_context.currency),
                ("Scale", data.content.table_context.scale),
                ("Measure", data.content.table_context.measure),
            )
            if val
        ]
        if parts:
            last_note_row += 1
            text_cell(sheet, last_note_row, note_column, "Context: " + "; ".join(parts))

    _style_table(sheet, data.content, note_column, last_note_row)

    if include_formulas:
        mismatch_fill = PatternFill("solid", fgColor="FBF0DE")
        declared_totals = set()
        for result in data.subtotal_results:
            group = result.get("group", {})
            total_id = group.get("total_cell_id")
            if not total_id:
                continue
            declared_totals.add(total_id)
            total_cell = cells_by_id.get(total_id)
            if total_cell is None or total_id in unreadable_ids:
                continue
            excel_target = sheet.cell(total_cell.row + 1, total_cell.col + 1)
            state = result.get("state")
            if state == "match":
                terms = [(t["cell_id"], int(t["sign"])) for t in group.get("terms", ())]
                formula = _build_excel_formula(cells_by_id, terms)
                if formula and typed_value(total_cell)[1] != "@":
                    excel_target.value = formula
            elif state == "difference":
                excel_target.fill = mismatch_fill

        for detected in data.detected_subtotals:
            total_id = detected.get("total_cell_id")
            if not total_id or total_id in declared_totals or total_id in unreadable_ids:
                continue
            total_cell = cells_by_id.get(total_id)
            if total_cell is None:
                continue
            excel_target = sheet.cell(total_cell.row + 1, total_cell.col + 1)
            state = detected.get("state")
            if state == "match":
                terms = [(cid, 1) for cid in detected.get("term_cell_ids", ())]
                formula = _build_excel_formula(cells_by_id, terms)
                if formula and typed_value(total_cell)[1] != "@":
                    excel_target.value = formula
            elif state == "difference":
                excel_target.fill = mismatch_fill


def _populate_subtotals_sheet(
    sheet, data: ExportInput, table_sheet_name: str, *, include_formulas: bool
):
    cells_by_id = {cell.id: cell for cell in data.content.cells}
    headers = [
        "Name",
        "Origin",
        "Total Cell",
        "Formula",
        "Reported Total",
        "Computed Sum",
        "Difference",
        "Status",
        "Notes",
    ]
    for col, header in enumerate(headers, 1):
        text_cell(sheet, 1, col, header)

    escaped_sheet = table_sheet_name.replace("'", "''")
    sheet_prefix = f"'{escaped_sheet}'!"
    declared_totals = set()
    rows_data = []

    for result in data.subtotal_results:
        group = result.get("group", {})
        total_id = group.get("total_cell_id")
        if total_id:
            declared_totals.add(total_id)
        total_cell = cells_by_id.get(total_id)
        total_coord = (
            f"{get_column_letter(total_cell.col + 1)}{total_cell.row + 1}" if total_cell else "—"
        )
        terms = [(t["cell_id"], int(t["sign"])) for t in group.get("terms", ())]
        display_formula = _build_excel_formula(cells_by_id, terms) or "—"
        cross_formula = _build_excel_formula(cells_by_id, terms, sheet_prefix=sheet_prefix)
        rows_data.append(
            {
                "name": group.get("name", "Subtotal"),
                "origin": "Declared",
                "total_coord": total_coord,
                "display_formula": display_formula,
                "cross_formula": cross_formula,
                "reported": result.get("reported_total"),
                "computed": result.get("input_sum"),
                "difference": result.get("difference"),
                "status": result.get("state", "unavailable"),
                "notes": result.get("reason", ""),
            }
        )

    for detected in data.detected_subtotals:
        total_id = detected.get("total_cell_id")
        if total_id in declared_totals:
            continue
        total_cell = cells_by_id.get(total_id)
        total_coord = (
            f"{get_column_letter(total_cell.col + 1)}{total_cell.row + 1}" if total_cell else "—"
        )
        terms = [(cid, 1) for cid in detected.get("term_cell_ids", ())]
        display_formula = _build_excel_formula(cells_by_id, terms) or "—"
        cross_formula = _build_excel_formula(cells_by_id, terms, sheet_prefix=sheet_prefix)
        direction = detected.get("direction", "vertical")
        rows_data.append(
            {
                "name": detected.get("label", "Detected subtotal"),
                "origin": f"Auto ({direction})",
                "total_coord": total_coord,
                "display_formula": display_formula,
                "cross_formula": cross_formula,
                "reported": detected.get("reported_total"),
                "computed": detected.get("input_sum"),
                "difference": detected.get("difference"),
                "status": detected.get("state", "match"),
                "notes": "Auto-detected arithmetic check",
            }
        )

    for row_idx, item in enumerate(rows_data, 2):
        text_cell(sheet, row_idx, 1, item["name"])
        text_cell(sheet, row_idx, 2, item["origin"])
        text_cell(sheet, row_idx, 3, item["total_coord"])
        text_cell(sheet, row_idx, 4, item["display_formula"])

        reported_cell = sheet.cell(row_idx, 5)
        if item["reported"] is not None:
            reported_cell.value = float(item["reported"])
            reported_cell.number_format = "#,##0.00;(#,##0.00)"
        else:
            text_cell(sheet, row_idx, 5, "—")

        computed_cell = sheet.cell(row_idx, 6)
        if include_formulas and item["cross_formula"]:
            computed_cell.value = item["cross_formula"]
            computed_cell.number_format = "#,##0.00;(#,##0.00)"
        elif item["computed"] is not None:
            computed_cell.value = float(item["computed"])
            computed_cell.number_format = "#,##0.00;(#,##0.00)"
        else:
            text_cell(sheet, row_idx, 6, "—")

        diff_cell = sheet.cell(row_idx, 7)
        if include_formulas and item["reported"] is not None and item["cross_formula"]:
            diff_cell.value = f"=E{row_idx}-F{row_idx}"
            diff_cell.number_format = "#,##0.00;(#,##0.00)"
        elif item["difference"] is not None:
            diff_cell.value = float(item["difference"])
            diff_cell.number_format = "#,##0.00;(#,##0.00)"
        else:
            text_cell(sheet, row_idx, 7, "—")

        text_cell(sheet, row_idx, 8, item["status"])
        text_cell(sheet, row_idx, 9, item["notes"])

    sheet.auto_filter.ref = sheet.dimensions
    widths = {
        "A": 34,
        "B": 16,
        "C": 12,
        "D": 22,
        "E": 18,
        "F": 18,
        "G": 16,
        "H": 20,
        "I": 48,
    }
    for col_ltr, width in widths.items():
        sheet.column_dimensions[col_ltr].width = width


def _style_metadata_sheet(sheet):
    sheet.sheet_view.showGridLines = False
    for row in sheet:
        for cell in row:
            cell.font = Font(name="Arial", size=10)
    for col in range(1, sheet.max_column + 1):
        if sheet.column_dimensions[get_column_letter(col)].width is None:
            sheet.column_dimensions[get_column_letter(col)].width = 24 if col > 1 else 40
    for row in range(1, sheet.max_row + 1):
        sheet.row_dimensions[row].height = 24
    for cell in sheet[1]:
        cell.font = Font(name="Arial", size=10, bold=True, color="233342")
        cell.fill = PatternFill("solid", fgColor="EAF0F5")


def _write_deterministic_xlsx(wb: Workbook, target: Path):
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
                root = ET.fromstring(value)
                for tag in ("created", "modified"):
                    element = root.find("{http://purl.org/dc/terms/}" + tag)
                    if element is not None:
                        element.text = "1980-01-01T00:00:00Z"
                value = ET.tostring(root, encoding="utf-8")
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(entry, value)


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
    if data.content.subtotal_groups or data.subtotal_results or data.detected_subtotals:
        sheet = wb.create_sheet("Subtotals")
        subtotal_sheets.append(sheet)
        _populate_subtotals_sheet(
            sheet, data, "Table", include_formulas=data.include_formulas
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

    _populate_table_sheet(table, data, manifest, include_formulas=data.include_formulas)

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
    for sheet in (source, changes, manifest_sheet, context_sheet, timeline_sheet):
        _style_metadata_sheet(sheet)
    for sheet in subtotal_sheets:
        sheet.sheet_view.showGridLines = False
        for cell in sheet[1]:
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

    _write_deterministic_xlsx(wb, target)
    return ExportArtifact(
        format="workbook",
        path=target,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        manifest=manifest,
    )


def write_report_workbook(report: ReportExportInput, target: Path) -> ExportArtifact:
    if not report.tables:
        raise DocumentError("no_tables", "Select at least one table to export", 422)
    wb = Workbook()
    overview = wb.active
    overview.title = "Overview"
    overview.sheet_view.showGridLines = False

    text_cell(overview, 1, 1, report.filename).font = Font(
        name="Arial", size=13, bold=True, color="233342"
    )
    text_cell(
        overview,
        2,
        1,
        f"Source SHA-256: {report.source_sha256[:16]}... · {len(report.tables)} table(s) · "
        + ("Live formulas enabled" if report.include_formulas else "Static values"),
    ).font = Font(name="Arial", size=10, color="607485")

    overview_headers = [
        "#",
        "Sheet",
        "Title / Label",
        "Page",
        "Shape",
        "Review Status",
        "Open Warnings",
        "Subtotals",
    ]
    for col, header in enumerate(overview_headers, 1):
        cell = text_cell(overview, 4, col, header)
        cell.font = Font(name="Arial", size=10, bold=True, color="233342")
        cell.fill = PatternFill("solid", fgColor="EAF0F5")

    used_titles = {"Overview", "Source & Audit"}
    audit_rows = []
    sheet_summaries = []

    for index, entry in enumerate(report.tables, 1):
        data = entry.export_input
        validate_grid(data.content)
        manifest = export_manifest(data, "workbook")
        sheet_name = _safe_sheet_title(
            data.content.title or entry.label, f"Table {index}", used_titles
        )
        ws = wb.create_sheet(sheet_name)
        _populate_table_sheet(ws, data, manifest, include_formulas=report.include_formulas)

        row_idx = 4 + index
        text_cell(overview, row_idx, 1, str(index))
        link_cell = text_cell(overview, row_idx, 2, sheet_name)
        escaped = sheet_name.replace("'", "''")
        link_cell.hyperlink = f"#'{escaped}'!A1"
        link_cell.font = Font(name="Arial", size=10, color="2447C9", underline="single")
        text_cell(overview, row_idx, 3, data.content.title or entry.label)
        text_cell(overview, row_idx, 4, f"p.{entry.page_no}")
        text_cell(overview, row_idx, 5, f"{data.content.rows} × {data.content.columns}")
        text_cell(
            overview,
            row_idx,
            6,
            {
                "draft": "Draft",
                "reviewed": "Reviewed",
                "reviewed_with_warnings": "Reviewed with warnings",
            }[manifest["review_status"]],
        )
        text_cell(overview, row_idx, 7, str(manifest["unresolved_count"]))
        subtotal_count = len(data.subtotal_results) + len(data.detected_subtotals)
        text_cell(overview, row_idx, 8, str(subtotal_count))

        sheet_summaries.append(
            {
                "table_id": str(entry.table_id),
                "sheet": sheet_name,
                "page": entry.page_no,
                "review_status": manifest["review_status"],
                "content_checksum": manifest["content_checksum"],
            }
        )
        for cell in data.content.cells:
            review = cell_review_metadata(data, cell)
            box = cell.reference.box
            box_str = (
                f"({box.left:.1f}, {box.top:.1f}, {box.right:.1f}, {box.bottom:.1f})"
                if box
                else "—"
            )
            audit_rows.append(
                [
                    sheet_name,
                    f"p.{cell.reference.page}",
                    f"{get_column_letter(cell.col + 1)}{cell.row + 1}",
                    cell.raw_text,
                    cell.text,
                    cell.role,
                    cell.interpretation,
                    review["verdict"],
                    cell.reference.precision,
                    box_str,
                ]
            )

    overview.auto_filter.ref = f"A4:H{4 + len(report.tables)}"
    for col_ltr, width in (
        ("A", 6),
        ("B", 28),
        ("C", 36),
        ("D", 10),
        ("E", 14),
        ("F", 22),
        ("G", 16),
        ("H", 12),
    ):
        overview.column_dimensions[col_ltr].width = width

    audit = wb.create_sheet("Source & Audit")
    _records(
        audit,
        [
            "Table",
            "Page",
            "Cell",
            "Raw Text",
            "Current Text",
            "Role",
            "Interpretation",
            "Verdict",
            "Source Precision",
            "Source Box",
        ],
        audit_rows,
    )
    _style_metadata_sheet(audit)
    for col_ltr, width in (
        ("A", 26),
        ("B", 8),
        ("C", 10),
        ("D", 28),
        ("E", 28),
        ("F", 16),
        ("G", 16),
        ("H", 18),
        ("I", 16),
        ("J", 32),
    ):
        audit.column_dimensions[col_ltr].width = width

    _write_deterministic_xlsx(wb, target)
    report_manifest = {
        "export_version": "1",
        "format": "report_workbook",
        "document_id": str(report.document_id),
        "filename": report.filename,
        "source_sha256": report.source_sha256,
        "include_formulas": report.include_formulas,
        "table_count": len(report.tables),
        "sheets": sheet_summaries,
        "generator": report.generation_manifest,
    }
    return ExportArtifact(
        format="report_workbook",
        path=target,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        manifest=report_manifest,
    )
