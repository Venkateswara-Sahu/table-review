"""Append-only ordered review events; historical independent records are retained."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0012"
down_revision = "0011"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "review_timeline_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("table_id", UUID, nullable=False),
        sa.Column("revision_id", UUID, sa.ForeignKey("table_revisions.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("cell_ids", JSONB, nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["table_id", "owner_id"], ["review_tables.id", "review_tables.owner_id"]
        ),
        sa.UniqueConstraint("table_id", "sequence", name="uq_review_timeline_sequence"),
    )
    for key in ("owner_id", "table_id", "revision_id"):
        op.create_index("ix_review_timeline_events_" + key, "review_timeline_events", [key])
    op.execute(
        "CREATE TRIGGER immutable_review_timeline BEFORE UPDATE ON review_timeline_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_update()"
    )


def downgrade():
    op.drop_table("review_timeline_events")
