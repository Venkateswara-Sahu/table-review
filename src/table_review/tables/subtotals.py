"""Explicit same-scope monetary relations; no inferred accounting identities."""

import hashlib
import json
import re
from decimal import Decimal, localcontext

from table_review.tables.context import ContextIndex
from table_review.tables.numbers import interpret_value
from table_review.tables.subtotal_types import SubtotalGroup, SubtotalResult

POLICY = "declared_sum_v1"


def _evidence(content, group, index, unreadable_ids=()):
    cells = {cell.id: cell for cell in content.cells}
    participants = [term.cell_id for term in group.terms] + [group.total_cell_id]
    scope = None
    records = []
    size = 0
    for identity in participants:
        if identity in unreadable_ids:
            raise ValueError(
                f"Cell {identity} is marked unreadable; resolve its source review first"
            )
        cell = cells.get(identity)
        if cell is None:
            raise ValueError(f"Cell {identity} no longer exists; select the subtotal inputs again")
        parsed = interpret_value(cell.text, cell.role, cell.interpretation)
        number = parsed.decimal_value
        if (
            len(cell.text) > 128
            or parsed.kind != "number"
            or number is None
            or number.as_tuple().exponent < -12
            or number.adjusted() > 14
        ):
            raise ValueError(
                f"Cell {identity} is not a supported explicit numeric amount; blanks, percentages and uncertain values are not zero"
            )
        context = index.cell(identity)
        bindings = {item.kind: item for item in context.confirmed}
        if not {"period", "currency", "scale"} <= bindings.keys():
            raise ValueError(
                f"Confirm current source context (period, currency and scale) for cell {identity}"
            )
        current = {kind: bindings[kind].value for kind in ("period", "currency", "scale")}
        if not re.search(
            r"\b(?:USD|EUR|GBP|INR|CAD|AUD|JPY|U\.S\.\s*dollars?)\b|US\$",
            current["currency"],
            re.IGNORECASE,
        ):
            raise ValueError(
                "Subtotal currency context needs an explicit currency code; a symbol alone is ambiguous"
            )
        if any(
            re.search(r"%|\b(?:percent(?:age)?|per[ -]share)\b", source.text, re.IGNORECASE)
            for source in (*context.column_headers, *context.row_headers)
        ):
            raise ValueError(
                "Percentage and per-share header context is outside additive currency-amount checks"
            )
        if scope is not None and scope != current:
            raise ValueError(
                "Subtotal context differs between cells; periods, currencies and scales must match exactly"
            )
        scope = current
        records.append(
            {
                "cell": cell.model_dump(mode="json"),
                "number": format(number, "f"),
                "context": {kind: bindings[kind].model_dump(mode="json") for kind in current},
                "sources": [source.model_dump(mode="json") for source in context.sources],
                "column_headers": [
                    source.model_dump(mode="json") for source in context.column_headers
                ],
                "row_headers": [source.model_dump(mode="json") for source in context.row_headers],
            }
        )
        size += len(json.dumps(records[-1], ensure_ascii=True).encode())
        if size > 128 * 1024:
            raise ValueError(
                "Subtotal source evidence exceeds its 128 KiB budget; use fewer inputs or simpler header context"
            )
    evidence = {
        "policy": POLICY,
        "scope": scope,
        "cells": records,
        "shape": [content.rows, content.columns],
        "headers": content.header_rows,
        "labels": content.row_label_columns,
        "total": group.total_cell_id,
        "terms": [term.model_dump() for term in group.terms],
        "tolerance": group.tolerance,
    }
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()
    return digest, scope, records


def confirm_group(content, group: SubtotalGroup):
    digest, _, _ = _evidence(content, group, ContextIndex(content))
    return group.model_copy(update={"evidence_fingerprint": digest})


def evaluate_subtotals(content, *, unreadable_ids=()):
    index = ContextIndex(content)
    results = []
    for group in content.subtotal_groups:
        try:
            digest, scope, records = _evidence(content, group, index, unreadable_ids)
        except ValueError as error:
            results.append(SubtotalResult(group=group, state="unavailable", reason=str(error)))
            continue
        with localcontext() as ctx:
            ctx.prec = 64
            summed = sum(
                (
                    Decimal(record["number"]) * term.sign
                    for record, term in zip(records, group.terms)
                ),
                Decimal(0),
            )
            reported = Decimal(records[-1]["number"])
            difference = reported - summed
            matched = abs(difference) <= Decimal(group.tolerance)
        current = (
            digest == group.evidence_fingerprint
            and group.confirmed_by is not None
            and group.confirmed_at is not None
        )
        state = "needs_confirmation" if not current else "match" if matched else "difference"
        reason = (
            "Participating values or source context changed; confirm this declared relation again"
            if not current
            else "Selected relation is within the declared absolute tolerance; this is not financial approval"
            if matched
            else "Reported total differs from this user-declared signed sum beyond its absolute tolerance; compare the source and the chosen relation"
        )
        results.append(
            SubtotalResult(
                group=group,
                state=state,
                reason=reason,
                scope=scope,
                inputs=tuple(
                    {**record, "sign": term.sign} for record, term in zip(records, group.terms)
                ),
                total=records[-1],
                input_sum=format(summed, "f"),
                reported_total=format(reported, "f"),
                difference=format(difference, "f"),
            )
        )
    return tuple(results)
