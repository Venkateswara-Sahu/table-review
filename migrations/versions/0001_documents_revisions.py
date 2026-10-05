"""Initial owner-scoped immutable table persistence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("byte_count", sa.Integer, nullable=False),
        sa.Column("page_count", sa.Integer, nullable=False),
        sa.Column("blob_key", sa.Text, nullable=False),
        sa.Column("preparation_state", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_foreground_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "owner_id", name="uq_document_owner"),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_table(
        "review_tables",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("current_revision_id", UUID, nullable=False),
        sa.Column("original_revision_id", UUID, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "owner_id", name="uq_table_owner"),
        sa.ForeignKeyConstraint(
            ["document_id", "owner_id"],
            ["documents.id", "documents.owner_id"],
            name="fk_table_document_owner",
        ),
    )
    op.create_index("ix_review_tables_owner_id", "review_tables", ["owner_id"])
    op.create_table(
        "table_revisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("table_id", UUID, nullable=False),
        sa.Column("parent_revision_id", UUID, sa.ForeignKey("table_revisions.id")),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("extraction_manifest", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["table_id", "owner_id"],
            ["review_tables.id", "review_tables.owner_id"],
            name="fk_revision_table_owner",
        ),
    )
    op.create_index("ix_table_revisions_owner_id", "table_revisions", ["owner_id"])
    op.create_index("ix_table_revisions_table_id", "table_revisions", ["table_id"])
    op.create_foreign_key(
        "fk_current_revision",
        "review_tables",
        "table_revisions",
        ["current_revision_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "edit_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("table_id", UUID, sa.ForeignKey("review_tables.id"), nullable=False),
        sa.Column("base_revision_id", UUID, sa.ForeignKey("table_revisions.id"), nullable=False),
        sa.Column("result_revision_id", UUID, sa.ForeignKey("table_revisions.id"), nullable=False),
        sa.Column("idempotency_key", UUID, nullable=False),
        sa.Column("operation", JSONB, nullable=False),
        sa.Column("affected_cell_ids", JSONB, nullable=False),
        sa.Column("structural_change", sa.Boolean, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_edit_owner_key"),
    )
    op.create_index("ix_edit_events_owner_id", "edit_events", ["owner_id"])
    op.execute("""CREATE FUNCTION reject_immutable_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Immutable record cannot be updated'; END; $$""")
    for table in ("table_revisions", "edit_events"):
        op.execute(
            f"CREATE TRIGGER immutable_update BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_immutable_update()"
        )


def downgrade():
    # Only explicit operator downgrade; ordinary application deletion uses its lifecycle.
    op.drop_table("edit_events")
    op.drop_constraint("fk_current_revision", "review_tables", type_="foreignkey")
    op.drop_table("table_revisions")
    op.drop_table("review_tables")
    op.drop_table("documents")
    op.execute("DROP FUNCTION reject_immutable_update()")
