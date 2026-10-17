"""Bound immutable event payloads before materializing their JSON in the API."""

from sqlalchemy import Text, cast, func, select

from table_review.reviews.models import CellReviewEventRow


class ReviewHistoryTooLarge(ValueError):
    pass


def bounded_events(session, revision_id, *, byte_limit, offset=0, page_size=None):
    payload_size = (
        func.octet_length(cast(CellReviewEventRow.cell_reviews, Text))
        + func.octet_length(cast(CellReviewEventRow.cell_ids, Text))
        + 512  # Identifiers, timestamps and JSON object overhead.
    )
    query = (
        select(CellReviewEventRow.id, payload_size)
        .where(CellReviewEventRow.revision_id == revision_id)
        .order_by(CellReviewEventRow.created_at, CellReviewEventRow.id)
        .offset(offset)
        .execution_options(yield_per=100)
    )
    if page_size is not None:
        query = query.limit(page_size + 1)
    identities, size, more = [], 0, False
    for identity, length in session.execute(query):
        if page_size is not None and len(identities) == page_size:
            more = True
            break
        if size + length > byte_limit:
            if page_size is None or not identities:
                raise ReviewHistoryTooLarge(
                    "Review history exceeds the safe payload budget; no history was silently omitted"
                )
            more = True
            break
        size += length
        identities.append(identity)
    rows = session.scalars(
        select(CellReviewEventRow)
        .where(CellReviewEventRow.id.in_(identities))
        .order_by(CellReviewEventRow.created_at, CellReviewEventRow.id)
    ).all()
    return rows, more
