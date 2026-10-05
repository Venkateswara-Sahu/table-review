"""Durable tasks, attempt tokens and immutable extraction runs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "tasks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("input", JSONB, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False),
        sa.Column("lease_token", UUID),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.Text),
        sa.Column("cancellation_requested", sa.Boolean, nullable=False),
        sa.Column("failure_code", sa.Text),
        sa.Column("result", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_task_owner_key"),
        sa.ForeignKeyConstraint(
            ["document_id", "owner_id"],
            ["documents.id", "documents.owner_id"],
            name="fk_task_document_owner",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 2", name="ck_task_attempt_limit"
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')", name="ck_task_state"
        ),
    )
    op.create_index("ix_tasks_owner_id", "tasks", ["owner_id"])
    op.create_index("ix_tasks_state", "tasks", ["state"])
    op.create_table(
        "task_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("task_id", UUID, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("token", UUID, nullable=False, unique=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.Text),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_task_attempt"),
    )
    op.create_table(
        "extraction_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("task_id", UUID, sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("page_info", JSONB, nullable=False),
        sa.Column("warnings", JSONB, nullable=False),
        sa.Column("table_ids", JSONB, nullable=False),
        sa.Column("source_tokens", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_extraction_runs_owner_id", "extraction_runs", ["owner_id"])
    op.execute(
        "CREATE TRIGGER immutable_extraction_run BEFORE UPDATE ON extraction_runs FOR EACH ROW EXECUTE FUNCTION reject_immutable_update()"
    )


def downgrade():
    op.drop_table("extraction_runs")
    op.drop_table("task_attempts")
    op.drop_table("tasks")
