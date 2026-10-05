"""Private accounts, revocable hashed sessions and persisted sign-in throttles."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("username", sa.String(80), unique=True, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("auth_epoch", UUID, nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "account_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("auth_epoch", UUID, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_account_sessions_owner_id", "account_sessions", ["owner_id"])
    op.create_table(
        "sign_in_throttles",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("sign_in_throttles")
    op.drop_table("account_sessions")
    op.drop_table("accounts")
