"""Recoverable table removal under parent-document retention."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = depends_on = None


def upgrade():
    op.add_column("review_tables", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "review_tables",
        sa.Column("removal_version", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("review_tables", "removal_version")
    op.drop_column("review_tables", "removed_at")
