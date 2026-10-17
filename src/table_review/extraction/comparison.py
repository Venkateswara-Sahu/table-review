"""Conservative source-grounded candidates and literal rerun differences.

Overlap proposes correspondence, not semantic identity. Never reuse review decisions.
"""

import hashlib
import json


def request_scope(payload):
    scope = {"pages": sorted(payload["pages"]), "region": payload.get("region")}
    return hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _overlap(before, after):
    left, right = before.source_reference, after.source_reference
    if not left or not right or left.page != right.page or not left.box or not right.box:
        return 0
    a, b = left.box, right.box
    if (a.page_width, a.page_height) != (b.page_width, b.page_height):
        return 0
    intersection = max(0, min(a.right, b.right) - max(a.left, b.left)) * max(
        0, min(a.bottom, b.bottom) - max(a.top, b.top)
    )
    union = (
        (a.right - a.left) * (a.bottom - a.top)
        + (b.right - b.left) * (b.bottom - b.top)
        - intersection
    )
    return intersection / union if union else 0


def match_tables(before, after):
    candidates = {
        key: [other for other, content in after.items() if _overlap(value, content) >= 0.7]
        for key, value in before.items()
    }
    reverse = {
        key: [other for other, values in candidates.items() if key in values] for key in after
    }
    matches, ambiguous = [], []
    for key, values in candidates.items():
        if len(values) == 1 and len(reverse[values[0]]) == 1:
            other = values[0]
            matches.append(
                {
                    "before_id": key,
                    "after_id": other,
                    "basis": "unique_source_overlap",
                    "source_iou": _overlap(before[key], after[other]),
                }
            )
        elif values:
            ambiguous.append({"before_id": key, "candidate_ids": sorted(values)})
    return {
        "matches": matches,
        "ambiguous": ambiguous,
        "unmatched_before": [key for key, values in candidates.items() if not values],
        "unmatched_after": [key for key, values in reverse.items() if not values],
        "matching_policy": "mutually_unique_same_page_source_iou_ge_0.7_v1",
        "identity_confirmed": False,
    }


def compare_tables(before, after):
    left, right = before.model_dump(mode="json"), after.model_dump(mode="json")
    table_fields = (
        "rows",
        "columns",
        "header_rows",
        "row_label_columns",
        "unit_notes",
        "source_reference",
    )
    table_changes = {
        key: {"before": left[key], "after": right[key]}
        for key in table_fields
        if left[key] != right[key]
    }
    a = {
        (cell["row"], cell["col"]): {k: v for k, v in cell.items() if k != "id"}
        for cell in left["cells"]
    }
    b = {
        (cell["row"], cell["col"]): {k: v for k, v in cell.items() if k != "id"}
        for cell in right["cells"]
    }
    changes = []
    for slot in sorted(a.keys() | b.keys()):
        old, new = a.get(slot), b.get(slot)
        if old != new:
            changes.append(
                {
                    "row": slot[0],
                    "col": slot[1],
                    "before": old,
                    "after": new,
                    "fields": sorted(
                        key
                        for key in (old or {}).keys() | (new or {}).keys()
                        if (old or {}).get(key) != (new or {}).get(key)
                    ),
                }
            )
    return {
        "table_changes": table_changes,
        "changed_cells": changes,
        "equal": not changes and not table_changes,
        "alignment": "physical_anchor_slots_not_inferred_financial_meaning",
    }
