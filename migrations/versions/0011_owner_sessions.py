"""Add the durable owner HTTP session table.

Revision ID: 0011_owner_sessions
Revises: 0010_backup_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_owner_sessions"
down_revision: str | None = "0010_backup_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owner_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_owner_sessions_expiry_after_creation"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_owner_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_owner_sessions_token_hash"),
    )
    op.create_index("ix_owner_sessions_expires_at", "owner_sessions", ["expires_at"], unique=False)


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM owner_sessions")).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: owner_sessions contains {count} session records; "
            "preserve them before downgrading"
        )
    op.drop_index("ix_owner_sessions_expires_at", table_name="owner_sessions")
    op.drop_table("owner_sessions")
