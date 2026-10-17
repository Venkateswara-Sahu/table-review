"""Retain explicit blank/unreadable decisions without rewriting historical events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = depends_on = None


def upgrade():
    for table in ("review_states", "review_snapshots", "cell_review_events"):
        op.add_column(table, sa.Column("cell_reviews", JSONB, nullable=False, server_default="{}"))
    op.add_column(
        "review_snapshots",
        sa.Column("unreadable_count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("review_snapshots", "unreadable_count")
    for table in ("cell_review_events", "review_snapshots", "review_states"):
        op.drop_column(table, "cell_reviews")
