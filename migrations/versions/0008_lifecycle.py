"""Lifecycle journal mirror and observable private-file cleanup."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0008"
down_revision = "0007"
branch_labels = depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("active_files_removed_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("cleanup_failure_code", sa.String(32)))
    op.add_column(
        "documents", sa.Column("cleanup_attempts", sa.Integer, nullable=False, server_default="0")
    )
    op.create_index("ix_document_expiry", "documents", ["deleted_at", "last_foreground_at"])
    op.create_table(
        "lifecycle_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("target_id", UUID, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("journal_sha256", sa.String(64), nullable=False),
    )
    for key in ("owner_id", "target_id", "at"):
        op.create_index(f"ix_lifecycle_events_{key}", "lifecycle_events", [key])
    op.execute(
        "CREATE TRIGGER immutable_lifecycle_event BEFORE UPDATE ON lifecycle_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_update()"
    )


def downgrade():
    op.drop_table("lifecycle_events")
    op.drop_index("ix_document_expiry")
    for key in ("active_files_removed_at", "cleanup_failure_code", "cleanup_attempts"):
        op.drop_column("documents", key)
