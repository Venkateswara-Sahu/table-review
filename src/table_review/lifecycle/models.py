from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from table_review.storage.models import Base


class LifecycleEventRow(Base):
    __tablename__ = "lifecycle_events"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    target_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    journal_sha256: Mapped[str] = mapped_column(String(64))


def mirror_event(session, entry):
    event = entry.event
    session.execute(
        insert(LifecycleEventRow)
        .values(
            id=event.id,
            owner_id=event.owner_id,
            target_id=event.target_id,
            kind=event.kind,
            at=event.at,
            payload=event.model_dump(mode="json"),
            journal_sha256=entry.sha256,
        )
        .on_conflict_do_nothing()
    )
