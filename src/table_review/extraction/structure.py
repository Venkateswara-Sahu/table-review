"""Normalize only omitted, empty row-label stubs across a contiguous header band."""

import re


def infer_aligned_row_labels(content):
    if content.row_label_columns or not content.header_rows or content.columns < 2:
        return content, ()
    rows = sorted(content.header_rows)
    if rows != list(range(len(rows))) or content.rows - len(rows) < 2:
        return content, ()
    if not any(c.col == 0 and c.row in rows and c.text.strip() for c in content.cells):
        return content, ()
    body = range(len(rows), content.rows)
    labels = [c for c in content.cells if c.col == 0 and c.row in body]
    if len(labels) != len(body) or any(
        c.row_span != 1
        or c.col_span != 1
        or not any(ch.isalpha() for ch in c.text)
        or c.role not in {"data", "label"}
        for c in labels
    ):
        return content, ()
    if len({" ".join(c.text.casefold().split()) for c in labels}) != len(labels):
        return content, ()
    numeric = re.compile(r"[+$€£−-]?\(?\d+(?:,\d{3})*(?:\.\d+)?%?\)?\Z")
    if not any(
        sum(
            c.col == col and c.row in body and bool(numeric.fullmatch(re.sub(r"\s+", "", c.text)))
            for c in content.cells
        )
        >= max(2, len(body) * 0.8)
        for col in range(1, content.columns)
    ):
        return content, ()
    ids = {c.id for c in labels}
    return content.model_copy(
        update={
            "row_label_columns": (0,),
            "cells": tuple(
                c.model_copy(update={"role": "row_header"}) if c.id in ids else c
                for c in content.cells
            ),
        }
    ), tuple({"cell_id": c.id, "reason": "aligned_text_identifier_column"} for c in labels)


def normalize_header_stubs(content):
    rows = sorted(content.header_rows)
    if not rows or rows != list(range(len(rows))):
        return content, ()
    cells = {cell.id: cell for cell in content.cells}
    changes = []
    for cell in tuple(cells.values()):
        if (
            cell.role == "data"
            and cell.col in content.row_label_columns
            and all(row in rows for row in range(cell.row, cell.row + cell.row_span))
        ):
            cells[cell.id] = cell.model_copy(update={"role": "column_header"})
            changes.append({"cell_id": cell.id, "reason": "row_label_header_role"})
    if len(rows) < 2:
        return content.model_copy(update={"cells": tuple(cells.values())}), tuple(changes)
    for column in content.row_label_columns:
        group = sorted(
            (c for c in cells.values() if c.col == column and c.row in rows),
            key=lambda c: c.row,
        )
        if len(group) != len(rows) or any(
            c.row != row
            or c.row_span != 1
            or c.col_span != 1
            or c.text != ""
            or c.raw_text != ""
            or c.interpretation != "uncertain"
            for row, c in zip(rows, group, strict=True)
        ):
            continue
        first, *removed = group
        cells[first.id] = first.model_copy(update={"row_span": len(rows)})
        for cell in removed:
            del cells[cell.id]
        changes.append(
            {
                "cell_id": first.id,
                "reason": "omitted_empty_header_stub",
                "removed_empty_cell_ids": [c.id for c in removed],
            }
        )
    return content.model_copy(update={"cells": tuple(cells.values())}), tuple(changes)
