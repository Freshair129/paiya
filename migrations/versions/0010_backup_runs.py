"""Record create, verify and restore backup operations.

Revision ID: 0010_backup_runs
Revises: 0009_purge_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_backup_runs"
down_revision: str | None = "0009_purge_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("db_bytes", sa.BigInteger(), nullable=True),
        sa.Column("media_count", sa.Integer(), nullable=True),
        sa.Column("media_bytes", sa.BigInteger(), nullable=True),
        sa.Column("set_name", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'failed')", name="ck_backup_runs_state"
        ),
        sa.CheckConstraint(
            "(db_bytes IS NULL OR db_bytes >= 0) AND "
            "(media_count IS NULL OR media_count >= 0) AND "
            "(media_bytes IS NULL OR media_bytes >= 0)",
            name="ck_backup_runs_nonnegative_sizes",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backup_runs"),
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM backup_runs")).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: backup_runs contains {count} backup records; "
            "preserve them before downgrading"
        )
    op.drop_table("backup_runs")
