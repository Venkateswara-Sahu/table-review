from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from table_review.storage.models import Base


class ReviewStateRow(Base):
    __tablename__ = "review_states"
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("table_revisions.id"), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    checked_cell_ids: Mapped[list] = mapped_column(JSONB, default=list)
    issue_decisions: Mapped[dict] = mapped_column(JSONB, default=dict)
    check_versions: Mapped[dict] = mapped_column(JSONB, default=lambda: {"checks": "not_run"})
    issues: Mapped[list] = mapped_column(JSONB, default=list)
    generation: Mapped[int] = mapped_column(default=0)
    current_approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("review_snapshots.id", use_alter=True, name="fk_current_approval")
    )


class ReviewSnapshotRow(Base):
    __tablename__ = "review_snapshots"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("table_revisions.id"), index=True)
    content_checksum: Mapped[str] = mapped_column(String(64))
    label: Mapped[str]
    checked_cell_ids: Mapped[list] = mapped_column(JSONB)
    issue_decisions: Mapped[dict] = mapped_column(JSONB)
    check_versions: Mapped[dict] = mapped_column(JSONB)
    issues: Mapped[list] = mapped_column(JSONB)
    checks_not_run: Mapped[bool]
    unresolved_count: Mapped[int]
    actor_id: Mapped[UUID] = mapped_column(index=True)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
