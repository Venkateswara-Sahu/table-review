"""Persist approval mode (standard exception-first vs strict_audit) on snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "review_snapshots",
        sa.Column(
            "approval_mode",
            sa.String(32),
            nullable=False,
            server_default="strict_audit",
        ),
    )


def downgrade():
    op.drop_column("review_snapshots", "approval_mode")
