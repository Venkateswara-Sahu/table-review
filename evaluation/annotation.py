"""Independent PDF-glyph transcription assistance, never extractor-generated gold.

The layout file is an authored annotation input. Its regions, column boundaries,
header groups and wrapped-row joins require source inspection before freezing.
This helper is not part of the production extractor or a human annotation study.
"""

import itertools
import json
import re
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfWriter
from pypdf.generic import ContentStream

from table_review.documents.pdf import source_tokens
from table_review.documents.types import PageBox, SourceReference
from table_review.extraction.types import SourceToken
from table_review.tables.types import CellContent, TableContent


def visible_text_copy(source: Path, destination: Path):
    """Omit invisible text-show operators; preserve visible text and geometry.

    Used only for independently transcribing sources containing hidden OCR.
    Production evaluation always feeds the unmodified, hashed source PDF.
    """
    writer = PdfWriter(clone_from=source)
    for page in writer.pages:
        stream = ContentStream(page.get_contents(), writer)
        mode, stack, retained = 0, [], []
        for operands, operator in stream.operations:
            if operator == b"q":
                stack.append(mode)
            elif operator == b"Q":
                mode = stack.pop() if stack else 0
            elif operator == b"Tr":
                mode = int(operands[0])
            if not (mode == 3 and operator in (b"Tj", b"TJ", b"'", b'"')):
                retained.append((operands, operator))
        stream.operations = retained
        page.replace_contents(stream)
    writer.write(destination)


def _center(token):
    return (token.box.left + token.box.right) / 2, (token.box.top + token.box.bottom) / 2


def _literal(tokens):
    # Dot leaders are drawn alignment decoration. U+FFFE is a broken mapping for
    # an authored hyphen in these sources; every occurrence requires visual audit.
    return " ".join(
        t.text.replace("\ufffe", "-") for t in tokens if not re.fullmatch(r"\.{3,}", t.text)
    )


def _reading_order(tokens):
    lines = []
    for token in sorted(tokens, key=lambda t: (_center(t)[1], t.box.left)):
        if not lines or _center(token)[1] - lines[-1][0] > 4:
            lines.append((_center(token)[1], []))
        lines[-1][1].append(token)
    return [t for _, line in lines for t in sorted(line, key=lambda t: t.box.left)]


def transcribe(layout, source: Path):
    region = PageBox.model_validate(layout["region"])
    bounds = layout["columns"]
    tokens = [
        t
        for t in source_tokens(source, region.page)
        if region.left <= _center(t)[0] <= region.right
        and region.top <= _center(t)[1] <= region.bottom
    ]
    if layout.get("token_splits"):
        with (
            pdfium.PdfDocument(source) as pdf,
            closing(pdf[region.page - 1]) as page,
            closing(page.get_textpage()) as textpage,
        ):
            if page.get_rotation() != 0:
                raise ValueError("Authored glyph split requires an unrotated page")
            crop_left, _, _, crop_top = page.get_bbox()
            split_tokens = []
            for token in tokens:
                rule = next((r for r in layout["token_splits"] if r["text"] == token.text), None)
                if rule is None:
                    split_tokens.append(token)
                    continue
                start = int(token.id.rsplit(":", 1)[1])
                for a, b in ((0, rule["offset"]), (rule["offset"], len(token.text))):
                    glyphs = [textpage.get_charbox(start + i) for i in range(a, b)]
                    box = region.model_copy(
                        update={
                            "left": min(g[0] for g in glyphs) - crop_left,
                            "right": max(g[2] for g in glyphs) - crop_left,
                            "top": crop_top - max(g[3] for g in glyphs),
                            "bottom": crop_top - min(g[1] for g in glyphs),
                        }
                    )
                    split_tokens.append(
                        SourceToken(
                            id=token.id + f":part:{a}",
                            text=token.text[a:b],
                            box=box,
                            provenance=SourceReference(
                                page=region.page, precision="cell", origin="user", box=box
                            ),
                        )
                    )
            tokens = split_tokens
    used = set()
    cells = []

    def add(row, col, chosen, *, role="data", row_span=1, col_span=1, literal=None):
        chosen = _reading_order(chosen)
        for token in chosen:
            if token.id in used:
                raise ValueError("An annotation token belongs to two cells")
            used.add(token.id)
        text = _literal(chosen) if literal is None else literal
        box = (
            region.model_copy(
                update={
                    "left": min(t.box.left for t in chosen),
                    "top": min(t.box.top for t in chosen),
                    "right": max(t.box.right for t in chosen),
                    "bottom": max(t.box.bottom for t in chosen),
                }
            )
            if chosen
            else None
        )
        cells.append(
            CellContent(
                id=f"{layout['id']}:{row}:{col}",
                row=row,
                col=col,
                row_span=row_span,
                col_span=col_span,
                raw_text=text,
                text=text,
                role=role,
                interpretation="text",
                reference=SourceReference(
                    page=region.page,
                    origin="user",
                    precision="cell" if box else "table",
                    box=box or region,
                ),
            )
        )

    for header in layout["headers"]:
        chosen = [
            t
            for t in tokens
            if bounds[header["col"]]
            <= _center(t)[0]
            < bounds[header["col"] + header.get("col_span", 1)]
            and header["top"] <= _center(t)[1] < header["bottom"]
        ]
        add(
            header["row"],
            header["col"],
            chosen,
            role=header.get("role", "column_header"),
            row_span=header.get("row_span", 1),
            col_span=header.get("col_span", 1),
            literal=header.get("literal"),
        )
    # Unit captions are metadata, not numeric grid rows. Preserve the exact
    # authored strings separately; do not count their exclusion as token loss.
    excluded = [
        t
        for t in tokens
        if any(top <= _center(t)[1] < bottom for top, bottom in layout.get("unit_bands", []))
    ]
    used.update(t.id for t in excluded)
    body = [t for t in tokens if t.id not in used]
    lines = []
    for token in sorted(body, key=lambda t: (_center(t)[1], t.box.left)):
        y = _center(token)[1]
        if not lines or y - lines[-1][0] > 4:
            lines.append((y, []))
        lines[-1][1].append(token)
    # Explicit source-authored joins, independent of extraction output.
    groups = []
    for y, line in lines:
        band = next(
            (i for i, (top, bottom) in enumerate(layout.get("joins", [])) if top <= y < bottom),
            None,
        )
        if groups and band is not None and groups[-1][0] == band:
            groups[-1][1].extend(line)
        else:
            groups.append((band, list(line)))
    row = layout["header_count"]
    for _, group in groups:
        columns = [
            [t for t in group if left <= _center(t)[0] < right]
            for left, right in itertools.pairwise(bounds)
        ]
        if {t.id for t in group} != {t.id for column in columns for t in column}:
            raise ValueError("Token outside annotated columns")
        for col, chosen in enumerate(columns):
            add(row, col, chosen, role="row_header" if col == 0 else "data")
        row += 1
    if len(used) != len(tokens):
        raise ValueError("Unassigned annotation tokens")
    return TableContent(
        rows=row,
        columns=len(bounds) - 1,
        cells=tuple(cells),
        header_rows=tuple(range(layout["header_count"])),
        row_label_columns=(0,),
        unit_notes=tuple(layout["unit_notes"]),
        source_reference=SourceReference(
            page=region.page, origin="user", precision="table", box=region
        ),
    ), {
        "selected_tokens": len(tokens),
        "unit_tokens": len(excluded),
        "assigned_tokens": len(used) - len(excluded),
        "unassigned_tokens": len(tokens) - len(used),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    layouts = json.loads(args.layout.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for layout in layouts:
        source = (args.layout.parent / layout["annotation_source_path"]).resolve()
        content, audit = transcribe(layout, source)
        (args.output / (layout["id"] + ".json")).write_text(
            content.model_dump_json(indent=2), encoding="utf-8"
        )
        summaries.append(
            {
                "id": layout["id"],
                **audit,
                "rows": content.rows,
                "columns": content.columns,
                "transcription": [
                    [c.text for c in content.cells if c.row == r] for r in range(content.rows)
                ],
            }
        )
    (args.output / "audit.json").write_text(
        json.dumps(summaries, indent=2, ensure_ascii=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
