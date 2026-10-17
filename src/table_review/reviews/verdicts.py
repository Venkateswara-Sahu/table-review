"""Source-backed review decisions and warnings, independent of numeric interpretation."""

import hashlib
from uuid import NAMESPACE_URL, uuid5

from table_review.checks.types import Issue
from table_review.reviews.types import CellReviewRecord


def record_for(
    cell, revision_id, verdict, *, actor_id=None, decided_at=None, reason="", origin="explicit"
):
    references = [cell["reference"], *cell.get("source_references", [])]
    return CellReviewRecord(
        verdict=verdict,
        revision_id=revision_id,
        actor_id=actor_id,
        decided_at=decided_at,
        reason=reason,
        source_references=tuple(references),
        origin=origin,
    ).model_dump(mode="json")


def normalize_legacy(state, revision):
    """Old nonempty checked flags remain legacy evidence; blank flags require a decision."""
    cells = {cell["id"]: cell for cell in revision.content["cells"]}
    records = dict(state.cell_reviews)
    for cell_id in state.checked_cell_ids:
        if cell_id not in records and cell_id in cells and cells[cell_id]["text"].strip():
            records[cell_id] = record_for(
                cells[cell_id], revision.id, "confirmed_value", origin="legacy"
            )
    checked = sorted(key for key, value in records.items() if value["verdict"] != "unreviewed")
    if set(checked) != set(state.checked_cell_ids):
        state.current_approval_id = None
        state.generation += 1
    state.cell_reviews = records
    state.checked_cell_ids = checked


def review_warnings(revision, records):
    issues = []
    for cell in revision.content["cells"]:
        record = records.get(cell["id"])
        if record and record["verdict"] == "unreadable":
            kind, reason = "unreadable_value", f"Source value is unreadable: {record['reason']}"
        elif not cell["text"].strip() and not record:
            kind, reason = (
                "unreviewed_blank",
                "Empty extraction position: confirm the source is blank or record it as unreadable.",
            )
        else:
            continue
        fingerprint = hashlib.sha256(f"{revision.id}:{cell['id']}:{kind}".encode()).hexdigest()
        issues.append(
            Issue(
                id=uuid5(NAMESPACE_URL, fingerprint),
                fingerprint=fingerprint,
                kind=kind,
                affected_cell_ids=(cell["id"],),
                evidence={"cell_review": record, "literal": cell["text"]},
                reason=reason,
                reference=cell["reference"],
            )
        )
    return tuple(issues)
