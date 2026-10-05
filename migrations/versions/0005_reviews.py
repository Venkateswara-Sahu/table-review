"""Revision-bound mutable checking state and immutable human approvals."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "review_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("revision_id", UUID, sa.ForeignKey("table_revisions.id"), nullable=False),
        sa.Column("content_checksum", sa.String(64), nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("checked_cell_ids", JSONB, nullable=False),
        sa.Column("issue_decisions", JSONB, nullable=False),
        sa.Column("check_versions", JSONB, nullable=False),
        sa.Column("issues", JSONB, nullable=False),
        sa.Column("checks_not_run", sa.Boolean, nullable=False),
        sa.Column("unresolved_count", sa.Integer, nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column(
            "approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "label IN ('reviewed','reviewed_with_warnings')", name="ck_review_label"
        ),
    )
    op.create_index("ix_review_snapshots_actor_id", "review_snapshots", ["actor_id"])
    op.create_index("ix_review_snapshots_revision_id", "review_snapshots", ["revision_id"])
    op.create_table(
        "review_states",
        sa.Column("revision_id", UUID, sa.ForeignKey("table_revisions.id"), primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("checked_cell_ids", JSONB, nullable=False),
        sa.Column("issue_decisions", JSONB, nullable=False),
        sa.Column("check_versions", JSONB, nullable=False),
        sa.Column("issues", JSONB, nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column(
            "current_approval_id",
            UUID,
            sa.ForeignKey("review_snapshots.id", name="fk_current_approval"),
        ),
    )
    op.create_index("ix_review_states_owner_id", "review_states", ["owner_id"])
    op.execute(
        "CREATE TRIGGER immutable_review_snapshot BEFORE UPDATE ON review_snapshots FOR EACH ROW EXECUTE FUNCTION reject_immutable_update()"
    )


def downgrade():
    op.drop_table("review_states")
    op.drop_table("review_snapshots")
