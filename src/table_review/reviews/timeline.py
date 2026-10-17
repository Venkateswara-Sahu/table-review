"""Ordered review evidence written under the existing document publication lock."""

import json

from sqlalchemy import Text, cast, func, literal, or_, select

from table_review.reviews.history import ReviewHistoryTooLarge
from table_review.reviews.models import TimelineEventRow
from table_review.reviews.types import TimelineEvent
from table_review.storage.models import RevisionRow
from table_review.storage.repository import _record

BUDGET = 8 * 1024**2


def append_event(
    session, revision, kind, *, actor_id=None, cell_ids=(), details=None, baseline_revision_id=None
):
    details = details or {}
    cell_ids = sorted(set(cell_ids))
    if len(json.dumps([details, cell_ids], ensure_ascii=True).encode()) + 4096 > BUDGET:
        raise ReviewHistoryTooLarge(
            "Timeline event exceeds the safe payload budget; the action was not saved"
        )
    latest = (
        session.scalar(
            select(func.max(TimelineEventRow.sequence)).where(
                TimelineEventRow.table_id == revision.table_id
            )
        )
        or 0
    )
    if latest == 0:
        session.add(
            TimelineEventRow(
                owner_id=revision.owner_id,
                table_id=revision.table_id,
                revision_id=baseline_revision_id or revision.id,
                sequence=1,
                kind="coverage_start",
                actor_id=None,
                cell_ids=[],
                details={
                    "reason": "Ordered recording begins here. Older independent history remains available; earlier event order was not reconstructed."
                },
            )
        )
        latest = 1
    session.add(
        TimelineEventRow(
            owner_id=revision.owner_id,
            table_id=revision.table_id,
            revision_id=revision.id,
            sequence=latest + 1,
            kind=kind,
            actor_id=actor_id,
            cell_ids=cell_ids,
            details=details,
        )
    )
    session.flush()


def ancestor_ids(session, revision):
    chain = (
        select(RevisionRow.id, RevisionRow.parent_revision_id, literal(1).label("depth"))
        .where(
            RevisionRow.id == revision.id,
            RevisionRow.owner_id == revision.owner_id,
            RevisionRow.table_id == revision.table_id,
        )
        .cte("timeline_ancestors", recursive=True)
    )
    chain = chain.union_all(
        select(RevisionRow.id, RevisionRow.parent_revision_id, chain.c.depth + 1)
        .join(chain, RevisionRow.id == chain.c.parent_revision_id)
        .where(
            RevisionRow.owner_id == revision.owner_id,
            RevisionRow.table_id == revision.table_id,
            chain.c.depth <= 10000,
        )
    )
    identities = list(session.scalars(select(chain.c.id)))
    if len(identities) > 10000 or len(set(identities)) != len(identities):
        raise ReviewHistoryTooLarge("Timeline revision ancestry exceeds the safe traversal budget")
    return set(identities)


def read_events(
    session, revision, *, after=0, through=None, cell_id=None, page_size=None, byte_limit=BUDGET
):
    ancestors = ancestor_ids(session, revision)
    base = (
        TimelineEventRow.owner_id == revision.owner_id,
        TimelineEventRow.table_id == revision.table_id,
    )
    first_future = session.scalar(
        select(func.min(TimelineEventRow.sequence)).where(
            *base, TimelineEventRow.revision_id.not_in(ancestors)
        )
    )
    latest = session.scalar(select(func.max(TimelineEventRow.sequence)).where(*base)) or 0
    cutoff = min(
        latest,
        first_future - 1 if first_future else latest,
        through if through is not None else latest,
    )
    conditions = (
        *base,
        TimelineEventRow.revision_id.in_(ancestors),
        TimelineEventRow.sequence > after,
        TimelineEventRow.sequence <= cutoff,
    )
    if cell_id:
        conditions = (
            *conditions,
            or_(TimelineEventRow.cell_ids.contains([cell_id]), TimelineEventRow.cell_ids == []),
        )
    size = (
        func.octet_length(cast(TimelineEventRow.details, Text))
        + func.octet_length(cast(TimelineEventRow.cell_ids, Text))
        + 512
    )
    query = (
        select(TimelineEventRow.id, size)
        .where(*conditions)
        .order_by(TimelineEventRow.sequence)
        .execution_options(yield_per=100)
    )
    if page_size is not None:
        query = query.limit(page_size + 1)
    identities, total, more = [], 0, False
    for identity, length in session.execute(query):
        if page_size is not None and len(identities) == page_size:
            more = True
            break
        if total + length > byte_limit:
            if page_size is None or not identities:
                raise ReviewHistoryTooLarge(
                    "Timeline exceeds the safe payload budget; no events were silently omitted"
                )
            more = True
            break
        total += length
        identities.append(identity)
    rows = session.scalars(
        select(TimelineEventRow)
        .where(TimelineEventRow.id.in_(identities))
        .order_by(TimelineEventRow.sequence)
    ).all()
    return tuple(_record(TimelineEvent, row) for row in rows), more, cutoff
