"""Hash page images; legacy cache entries require regeneration."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = depends_on = None


def upgrade():
    op.add_column("page_artifacts", sa.Column("sha256", sa.String(64)))


def downgrade():
    op.drop_column("page_artifacts", "sha256")
