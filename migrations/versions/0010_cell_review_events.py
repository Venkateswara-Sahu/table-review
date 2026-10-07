"""Retain actor/time for actual checked-state changes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0010"
down_revision = "0009"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "cell_review_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("revision_id", UUID, sa.ForeignKey("table_revisions.id"), nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("cell_ids", JSONB, nullable=False),
        sa.Column("checked", sa.Boolean, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    for key in ("revision_id", "actor_id"):
        op.create_index(f"ix_cell_review_events_{key}", "cell_review_events", [key])
    op.execute(
        "CREATE TRIGGER immutable_cell_review BEFORE UPDATE ON cell_review_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_update()"
    )


def downgrade():
    op.drop_table("cell_review_events")
