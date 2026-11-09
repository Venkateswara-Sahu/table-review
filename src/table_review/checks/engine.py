import hashlib
import json
import re
from collections import Counter, defaultdict
from itertools import pairwise
from uuid import NAMESPACE_URL, uuid5

from table_review.checks.types import CheckContext, Issue
from table_review.tables.subtotals import evaluate_subtotals
from table_review.tables.types import TableContent

CHECK_VERSION = "2"


def normalize(text):
    """Only collapse ASCII whitespace and NBSP; preserve signs and punctuation."""
    return re.sub(r"[ \t\r\n\f\v\u00a0]+", " ", text).strip(" ")


def _numeric(text):
    # A candidate may be a header, note or dash; this is not financial typing.
    return bool(re.search(r"[0-9]", text)) or text in ("-", "–", "—", "−")


def _contains(box, token):
    area = token.box
    return (
        box.page == area.page
        and box.left <= (area.left + area.right) / 2 <= box.right
        and box.top <= (area.top + area.bottom) / 2 <= box.bottom
    )


def _identity(token):
    # Same native location is one piece of evidence even if an adapter repeats it
    # under another ID. Equal values elsewhere are different identities.
    box = token.box
    return (box.page, box.left, box.top, box.right, box.bottom)


def evaluate_checks(content: TableContent, context: CheckContext) -> tuple[Issue, ...]:
    issues = []
    cells = {cell.id: cell for cell in content.cells}

    def add(kind, ids, evidence, reason, reference=None):
        ids = tuple(sorted(set(ids)))
        signature = {
            "version": context.check_version,
            "kind": kind,
            "cells": [cells[id].model_dump(mode="json") for id in ids],
            "evidence": evidence,
            "reference": reference.model_dump(mode="json") if reference else None,
            "associations": {
                "rows": content.rows,
                "columns": content.columns,
                "header_rows": content.header_rows,
                "row_label_columns": content.row_label_columns,
                "unit_notes": content.unit_notes,
            },
            "source_regions": [r.model_dump(mode="json") for r in context.source_regions],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                signature, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        issues.append(
            Issue(
                id=uuid5(NAMESPACE_URL, "table-review:issue:" + fingerprint),
                kind=kind,
                affected_cell_ids=ids,
                evidence=evidence,
                fingerprint=fingerprint,
                reason=reason,
                reference=reference,
            )
        )

    unique = {}
    for token in sorted(context.source_tokens, key=lambda item: item.id):
        unique.setdefault(_identity(token), token)
    tokens = tuple(unique.values())
    scoped = tuple(
        t for t in tokens if any(_contains(region, t) for region in context.source_regions)
    )
    matched = set()
    used_by = defaultdict(set)
    coarse = []
    precise = []
    for cell in content.cells:
        if cell.reference.precision != "cell" or cell.reference.box is None:
            # An empty structural position makes no placement claim. Blank
            # data positions remain reviewable through unreviewed_blank.
            if cell.text.strip():
                coarse.append(cell)
            continue
        precise.append(cell)
        literals = Counter(normalize(cell.text).split(" "))
        available = defaultdict(list)
        for token in tokens:
            if _contains(cell.reference.box, token):
                available[normalize(token.text)].append(token)
        for literal, choices in available.items():
            if literal not in literals:
                continue
            for token in choices[: literals[literal]]:
                matched.add(token.id)
                used_by[token.id].add(cell.id)
            if _numeric(literal) and literals[literal] > len(choices):
                add(
                    "duplicate_source_token",
                    (cell.id,),
                    {
                        "token_ids": [t.id for t in choices],
                        "literal": literal,
                        "occurrences_in_cell": literals[literal],
                        "source_locations": len(choices),
                    },
                    "This cell may repeat a value from the same source location. Check whether the extra occurrence belongs here.",
                    choices[0].provenance,
                )
    for token in tokens:
        if len(used_by[token.id]) > 1:
            add(
                "duplicate_source_token",
                used_by[token.id],
                {
                    "token_ids": [token.id],
                    "literal": token.text,
                    "box": token.box.model_dump(mode="json"),
                },
                "Several cells may reuse the same source token and location. Equal values at different locations are allowed.",
                token.provenance,
            )

    # Coarse references can support literal coverage counts, but cannot establish
    # which particular cell owns a native location or a placement discrepancy.
    remaining = defaultdict(list)
    for token in scoped:
        if token.id not in matched:
            remaining[normalize(token.text)].append(token)
    for cell in coarse:
        for literal in normalize(cell.text).split(" "):
            if remaining[literal]:
                matched.add(remaining[literal].pop(0).id)
    if not context.source_available or not context.source_regions:
        add(
            "coverage_unavailable",
            (),
            {
                "source_available": context.source_available,
                "region_available": bool(context.source_regions),
            },
            "Numeric coverage could not be checked against a bounded original source region. Human review is still required.",
            content.source_reference,
        )
    else:
        for token in scoped:
            if _numeric(normalize(token.text)) and token.id not in matched:
                y = (token.box.top + token.box.bottom) / 2
                related = [
                    c.id
                    for c in precise
                    if c.reference.box.page == token.box.page
                    and c.reference.box.top - 2 <= y <= c.reference.box.bottom + 2
                ]
                add(
                    "coverage_discrepancy",
                    related,
                    {
                        "token_ids": [token.id],
                        "literal": token.text,
                        "box": token.box.model_dump(mode="json"),
                    },
                    "A numeric token inside this source region may be missing or have a different literal in the table. It may also be a header or note; compare the source before deciding.",
                    token.provenance,
                )

    if coarse:
        add(
            "limited_reference",
            (c.id for c in coarse),
            {"precisions": sorted({c.reference.precision for c in coarse})},
            "Exact cell geometry is unavailable for these cells. Their broader references do not establish placement correctness.",
            coarse[0].reference,
        )

    # Compare neighboring physical cells along occupied grid axes. This is
    # O(grid slots log grid slots), rather than a quadratic all-cell comparison.
    seen_pairs = set()
    for axis in ("row", "col"):
        groups = defaultdict(list)
        other, span = ("col", "col_span") if axis == "row" else ("row", "row_span")
        for cell in precise:
            for index in range(getattr(cell, other), getattr(cell, other) + getattr(cell, span)):
                groups[index].append(cell)
        for group in groups.values():
            ordered = sorted(group, key=lambda c: (getattr(c, axis), c.id))
            for first, second in pairwise(ordered):
                if first.reference.page != second.reference.page:
                    continue
                a, b = first.reference.box, second.reference.box
                centre_a, centre_b = (
                    ((a.top + a.bottom) / 2, (b.top + b.bottom) / 2)
                    if axis == "row"
                    else ((a.left + a.right) / 2, (b.left + b.right) / 2)
                )
                key = (axis, first.id, second.id)
                if centre_a > centre_b + 2 and key not in seen_pairs:
                    seen_pairs.add(key)
                    add(
                        "placement_ambiguity",
                        (first.id, second.id),
                        {
                            "axis": axis,
                            "centre_points": [centre_a, centre_b],
                            "tolerance_points": 2,
                        },
                        "Source geometry runs in a different order from the declared grid. Rotation, layout or placement may need review.",
                        first.reference,
                    )

    occupied = set()
    for cell in content.cells:
        for row in range(cell.row, cell.row + cell.row_span):
            for col in range(cell.col, cell.col + cell.col_span):
                occupied.add((row, col))
    missing_slots = content.rows * content.columns - len(occupied)
    bad_roles = [
        c.id
        for c in content.cells
        if (
            c.row in content.header_rows
            and c.col not in content.row_label_columns
            and c.role != "column_header"
        )
        or (
            c.col in content.row_label_columns
            and c.row not in content.header_rows
            and c.role not in ("row_header", "section", "label")
        )
        or (
            c.role == "column_header"
            and not any(r in content.header_rows for r in range(c.row, c.row + c.row_span))
        )
        or (
            c.role == "row_header"
            and not any(
                col in content.row_label_columns for col in range(c.col, c.col + c.col_span)
            )
        )
    ]
    # Absent slots can be intentional blanks. Without source evidence they do
    # not establish a missing value or a broken association by themselves.
    if bad_roles:
        add(
            "structural_inconsistency",
            bad_roles,
            {
                "missing_grid_slots": missing_slots,
                "inconsistent_header_cell_ids": sorted(bad_roles),
            },
            "Header roles disagree with the declared row or column associations. Check the structure; an absent physical slot alone is not an inferred missing value or zero.",
            content.source_reference,
        )
    for result in evaluate_subtotals(content, unreadable_ids=context.unreadable_cell_ids):
        if result.state == "match":
            continue
        ids = {term.cell_id for term in result.group.terms} | {result.group.total_cell_id}
        add(
            "subtotal_difference" if result.state == "difference" else "subtotal_unavailable",
            ids & cells.keys(),
            result.model_dump(mode="json"),
            result.reason,
            cells[result.group.total_cell_id].reference
            if result.group.total_cell_id in cells
            else content.source_reference,
        )
    return tuple(issues)
