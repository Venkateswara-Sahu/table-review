"""Append-only human issue decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006"
down_revision = "0005"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "issue_decision_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("revision_id", UUID, sa.ForeignKey("table_revisions.id"), nullable=False),
        sa.Column("issue_id", UUID, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("decision", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_issue_decision_events_owner_id", "issue_decision_events", ["owner_id"])
    op.create_index(
        "ix_issue_decision_events_revision_id", "issue_decision_events", ["revision_id"]
    )
    op.execute(
        "CREATE TRIGGER immutable_issue_decision BEFORE UPDATE ON issue_decision_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_update()"
    )


def downgrade():
    op.drop_table("issue_decision_events")
