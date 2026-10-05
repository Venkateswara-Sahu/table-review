"""Account-scoped upload idempotency and private page renders."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "upload_requests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id"), nullable=False),
        sa.UniqueConstraint("owner_id", "key", name="uq_upload_owner_key"),
    )
    op.create_index("ix_upload_requests_owner_id", "upload_requests", ["owner_id"])
    op.create_table(
        "page_artifacts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("page_no", sa.Integer, nullable=False),
        sa.Column("scale", sa.Float, nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("blob_key", sa.Text, nullable=False),
        sa.Column("width", sa.Float, nullable=False),
        sa.Column("height", sa.Float, nullable=False),
        sa.Column("pixel_width", sa.Integer, nullable=False),
        sa.Column("pixel_height", sa.Integer, nullable=False),
        sa.UniqueConstraint("owner_id", "document_id", "page_no", "scale", name="uq_page_render"),
        sa.ForeignKeyConstraint(
            ["document_id", "owner_id"],
            ["documents.id", "documents.owner_id"],
            name="fk_page_document_owner",
        ),
    )
    op.create_index("ix_page_artifacts_owner_id", "page_artifacts", ["owner_id"])


def downgrade():
    op.drop_table("page_artifacts")
    op.drop_table("upload_requests")
