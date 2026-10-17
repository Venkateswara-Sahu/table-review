"""Conservative native-text reconciliation; preserve parser literals in raw_text.

No OCR substitutions, numeric repairs, grid inference or unit confirmation.
Every changed display literal requires precise visible source-token evidence.
"""

import hashlib
import re
from collections import defaultdict
from itertools import pairwise

from table_review.documents.types import SourceReference

_TYPOGRAPHY = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": "-", "–": "-"})
_AMOUNT = re.compile(r"(?:\([0-9][0-9,]*(?:\.[0-9]+)?\)|-?[0-9][0-9,]*(?:\.[0-9]+)?)")


def _fold(text):
    return " ".join(text.translate(_TYPOGRAPHY).split())


def _inside(token, cell):
    box = cell.reference.box
    return (
        cell.reference.precision == "cell"
        and box is not None
        and token.box.page == box.page
        and abs(token.box.page_width - box.page_width) < 0.01
        and abs(token.box.page_height - box.page_height) < 0.01
        and box.left - 0.5 <= (token.box.left + token.box.right) / 2 <= box.right + 0.5
        and box.top - 0.5 <= (token.box.top + token.box.bottom) / 2 <= box.bottom + 0.5
    )


def _ordered(tokens):
    lines = []
    for token in sorted(tokens, key=lambda t: ((t.box.top + t.box.bottom) / 2, t.box.left)):
        center = (token.box.top + token.box.bottom) / 2
        if not lines or center - lines[-1][0] > 3:
            lines.append((center, []))
        lines[-1][1].append(token)
    return tuple(t for _, line in lines for t in sorted(line, key=lambda t: t.box.left))


def reconcile_native_literals(content, tokens):
    cells = {cell.id: cell for cell in content.cells}
    changes = []

    def replace(cell, text, evidence, reason):
        if text == cell.text:
            return
        original = evidence[0].box
        box = original.model_copy(
            update={
                "left": min(t.box.left for t in evidence),
                "right": max(t.box.right for t in evidence),
                "top": min(t.box.top for t in evidence),
                "bottom": max(t.box.bottom for t in evidence),
            }
        )
        cells[cell.id] = cell.model_copy(
            update={
                "text": text,
                "reference": SourceReference(page=box.page, precision="cell", box=box),
                "source_references": tuple(t.provenance for t in evidence),
            }
        )
        changes.append(
            {
                "cell_id": cell.id,
                "reason": reason,
                "parser_text_sha256": hashlib.sha256(cell.raw_text.encode()).hexdigest(),
                "display_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "source_token_ids": [t.id for t in evidence],
            }
        )

    rows = defaultdict(list)
    for cell in content.cells:
        if (
            cell.row not in content.header_rows
            and cell.role in ("data", "value")
            and cell.row_span == cell.col_span == 1
        ):
            amount = cell.text.replace("$", "").strip()
            if _AMOUNT.fullmatch(amount):
                rows[cell.row].append((cell, amount))
    for row in rows.values():
        if len(row) < 2:
            continue
        evidence = []
        for cell, amount in row:
            values = [t for t in tokens if t.text == amount and _inside(t, cell)]
            if len(values) != 1:
                break
            value = values[0]
            center = (value.box.top + value.box.bottom) / 2
            preceding = [
                t
                for t in tokens
                if t.box.page == value.box.page
                and abs((t.box.top + t.box.bottom) / 2 - center)
                <= max(2, (value.box.bottom - value.box.top) * 0.6)
                and t.box.right <= value.box.left + 0.1
            ]
            prefix = max(preceding, key=lambda t: t.box.right, default=None)
            if prefix is None or prefix.text != "$" or value.box.left - prefix.box.right > 60:
                break
            evidence.append((cell, amount, prefix, value))
        # Require the complete row's amounts to have distinct prefix markers.
        # This rejects postfix notation and partial/missing source evidence.
        if len(evidence) != len(row) or len({e[2].id for e in evidence}) != len(row):
            continue
        # A leading unrelated dollar plus postfix markers can look like a
        # shifted prefix sequence. Every dollar in this row's source extent
        # must be accounted for, including any marker after the last amount.
        first = min(evidence, key=lambda item: item[3].box.left)
        center = (first[3].box.top + first[3].box.bottom) / 2
        region = content.source_reference.box if content.source_reference else None
        left = region.left if region else min(item[2].box.left for item in evidence)
        right = (
            region.right if region else max(item[0].reference.box.right for item in evidence) + 60
        )
        row_markers = {
            token.id
            for token in tokens
            if token.text == "$"
            and token.box.page == first[3].box.page
            and left <= token.box.left <= right
            and abs((token.box.top + token.box.bottom) / 2 - center)
            <= max(2, (first[3].box.bottom - first[3].box.top) * 0.6)
        }
        if row_markers != {item[2].id for item in evidence}:
            continue
        for cell, amount, prefix, value in evidence:
            replace(cell, "$ " + amount, (prefix, value), "source_prefix_currency_row")

    for cell in tuple(cells.values()):
        evidence = _ordered([t for t in tokens if _inside(t, cell)])
        literal = " ".join(t.text for t in evidence)
        if evidence and literal != cell.text and _fold(literal) == _fold(cell.text):
            replace(cell, literal, evidence, "source_typographic_literal")
    return content.model_copy(update={"cells": tuple(cells.values())}), tuple(changes)


def reconcile_ocr_currency_rows(content, tokens):
    """Regroup complete recognized prefix sequences, never split OCR glyph boxes.

    Some OCR chunks contain an amount followed by the next column's dollar.
    Require the entire ordered sequence `$`, `amount $`, ..., `amount`.
    Digits must already agree with the parser; an S or missing marker rejects
    the row. Chunk evidence cannot ground the moved character precisely, so
    repaired cells explicitly downgrade to table precision.
    """
    if not content.source_reference or not content.source_reference.box:
        return content, ()
    region = content.source_reference.box
    rows = defaultdict(list)
    cells = {cell.id: cell for cell in content.cells}
    changes = []
    for cell in content.cells:
        if (
            cell.row not in content.header_rows
            and cell.role in ("data", "value")
            and cell.row_span == cell.col_span == 1
        ):
            amount = cell.text.replace("$", "").strip()
            if _AMOUNT.fullmatch(amount):
                rows[cell.row].append((cell, amount))
    for row in rows.values():
        row.sort(key=lambda item: item[0].col)
        if len(row) < 2:
            continue
        chunks = []
        for index, (cell, amount) in enumerate(row):
            expected = amount + (" $" if index < len(row) - 1 else "")
            chosen = [
                t for t in tokens if _inside(t, cell) and " ".join(t.text.split()) == expected
            ]
            if len(chosen) != 1:
                break
            chunks.append(chosen[0])
        if len(chunks) != len(row):
            continue
        if any(a.box.right >= b.box.left for a, b in pairwise(chunks)):
            continue
        first = chunks[0]
        center = (first.box.top + first.box.bottom) / 2
        on_line = [
            t
            for t in tokens
            if t.box.page == region.page
            and region.left <= t.box.left <= region.right
            and abs((t.box.top + t.box.bottom) / 2 - center)
            <= max(2, (first.box.bottom - first.box.top) * 0.6)
        ]
        preceding = max(
            (t for t in on_line if t.box.right <= first.box.left),
            key=lambda t: t.box.right,
            default=None,
        )
        if preceding is None or preceding.text != "$" or first.box.left - preceding.box.right > 60:
            continue
        if sum(t.text.count("$") for t in on_line) != len(row):
            continue
        if any(not _inside(chunk, cell) for chunk, (cell, _) in zip(chunks, row)):
            continue
        for index, (cell, amount) in enumerate(row):
            text = "$ " + amount
            if text == cell.text:
                continue
            previous = preceding if index == 0 else chunks[index - 1]
            evidence = previous, chunks[index]
            cells[cell.id] = cell.model_copy(
                update={
                    "text": text,
                    "reference": content.source_reference.model_copy(update={"precision": "table"}),
                    "source_references": tuple(
                        t.provenance.model_copy(update={"precision": "table"}) for t in evidence
                    ),
                }
            )
            changes.append(
                {
                    "cell_id": cell.id,
                    "reason": "ocr_complete_prefix_currency_sequence",
                    "parser_text_sha256": hashlib.sha256(cell.raw_text.encode()).hexdigest(),
                    "display_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "source_token_ids": [t.id for t in evidence],
                    "grounding": "coarse_ocr_chunks_not_individual_currency_glyphs",
                }
            )
    return content.model_copy(update={"cells": tuple(cells.values())}), tuple(changes)
