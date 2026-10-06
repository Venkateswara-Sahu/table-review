from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from table_review.documents.types import ImmutableModel
from table_review.tables.types import TableContent


class Base(DeclarativeBase):
    pass


class AccountRow(Base):
    __tablename__ = "accounts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str]
    auth_epoch: Mapped[UUID] = mapped_column(default=uuid4)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionRow(Base):
    __tablename__ = "account_sessions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    auth_epoch: Mapped[UUID]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SignInThrottleRow(Base):
    __tablename__ = "sign_in_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UploadRequestRow(Base):
    __tablename__ = "upload_requests"
    __table_args__ = (UniqueConstraint("owner_id", "key", name="uq_upload_owner_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    key: Mapped[str] = mapped_column(String(128))
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"))


class PageArtifactRow(Base):
    __tablename__ = "page_artifacts"
    __table_args__ = (
        UniqueConstraint("owner_id", "document_id", "page_no", "scale", name="uq_page_render"),
        ForeignKeyConstraint(
            ["document_id", "owner_id"],
            ["documents.id", "documents.owner_id"],
            name="fk_page_document_owner",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID]
    page_no: Mapped[int]
    scale: Mapped[float] = mapped_column(Float)
    source_sha256: Mapped[str] = mapped_column(String(64))
    blob_key: Mapped[str]
    sha256: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[float] = mapped_column(Float)
    height: Mapped[float] = mapped_column(Float)
    pixel_width: Mapped[int]
    pixel_height: Mapped[int]


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("id", "owner_id", name="uq_document_owner"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    filename: Mapped[str]
    source_sha256: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int]
    page_count: Mapped[int]
    blob_key: Mapped[str]
    preparation_state: Mapped[str] = mapped_column(default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_foreground_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_files_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_failure_code: Mapped[str | None] = mapped_column(String(32))
    cleanup_attempts: Mapped[int] = mapped_column(default=0, server_default="0")


class TableRow(Base):
    __tablename__ = "review_tables"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_table_owner"),
        ForeignKeyConstraint(
            ["document_id", "owner_id"],
            ["documents.id", "documents.owner_id"],
            name="fk_table_document_owner",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID]
    origin: Mapped[str] = mapped_column(default="extracted")
    current_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "table_revisions.id",
            use_alter=True,
            name="fk_current_revision",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    original_revision_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RevisionRow(Base):
    __tablename__ = "table_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["table_id", "owner_id"],
            ["review_tables.id", "review_tables.owner_id"],
            name="fk_revision_table_owner",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    table_id: Mapped[UUID] = mapped_column(index=True)
    parent_revision_id: Mapped[UUID | None] = mapped_column(ForeignKey("table_revisions.id"))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    extraction_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EditEventRow(Base):
    __tablename__ = "edit_events"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_edit_owner_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    table_id: Mapped[UUID] = mapped_column(ForeignKey("review_tables.id"))
    base_revision_id: Mapped[UUID] = mapped_column(ForeignKey("table_revisions.id"))
    result_revision_id: Mapped[UUID] = mapped_column(ForeignKey("table_revisions.id"))
    idempotency_key: Mapped[UUID]
    operation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    affected_cell_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    structural_change: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EditEventInput(ImmutableModel):
    idempotency_key: UUID
    operation: dict[str, Any]
    affected_cell_ids: tuple[str, ...] = ()
    structural_change: bool = False


class DocumentRecord(ImmutableModel):
    id: UUID
    owner_id: UUID
    filename: str
    source_sha256: str
    byte_count: int
    page_count: int
    blob_key: str
    preparation_state: str
    created_at: datetime
    last_foreground_at: datetime
    deleted_at: datetime | None = None


class TableRecord(ImmutableModel):
    id: UUID
    owner_id: UUID
    document_id: UUID
    current_revision_id: UUID
    original_revision_id: UUID
    origin: str


class RevisionRecord(ImmutableModel):
    id: UUID
    owner_id: UUID
    table_id: UUID
    parent_revision_id: UUID | None
    content: TableContent
    checksum: str
    extraction_manifest: dict[str, Any]
    created_at: datetime


class BlobRecord(ImmutableModel):
    owner_id: UUID
    key: str
    sha256: str
    byte_count: int
