"""Deduplicated captured context evidence with an explicit expanded-JSON bound."""

import json

from table_review.documents.errors import DocumentError
from table_review.tables.context import ContextIndex


def context_package(content):
    index = ContextIndex(content)
    sources, proposals, cells = {}, {}, []
    expanded_bytes = 0

    def account(value):
        nonlocal expanded_bytes
        expanded_bytes += len(json.dumps(value, ensure_ascii=True).encode())
        if expanded_bytes > 16 * 1024**2:
            raise DocumentError(
                "context_export_limit", "Context evidence exceeds the 16 MiB export budget"
            )

    for cell in content.cells:
        view = index.cell(cell.id)
        for source in view.sources:
            if source.id not in sources:
                sources[source.id] = source.model_dump(mode="json")
                account({source.id: sources[source.id]})
        for item in view.proposals:
            if item.id not in proposals:
                proposals[item.id] = item.model_dump(mode="json")
                account({item.id: proposals[item.id]})
        cells.append(
            {
                "cell_id": cell.id,
                "column_header_ids": [source.id for source in view.column_headers],
                "row_header_ids": [source.id for source in view.row_headers],
                "proposal_ids": [item.id for item in view.proposals],
                "confirmed": [item.model_dump(mode="json") for item in view.confirmed],
                "warnings": list(view.warnings),
            }
        )
        account(cells[-1])
    value = {
        "schema": "source_context_v1",
        "semantics": "source_literals_not_scale_conversion",
        "sources": sources,
        "proposals": proposals,
        "cells": cells,
    }
    if len(json.dumps(value, ensure_ascii=True).encode()) > 16 * 1024**2:
        raise DocumentError(
            "context_export_limit", "Context evidence exceeds the 16 MiB export budget"
        )
    return value


def context_records(package):
    for group in ("sources", "proposals"):
        for identity, value in package[group].items():
            yield group + ":" + identity, value
    for cell in package["cells"]:
        yield "cell:" + cell["cell_id"], cell
