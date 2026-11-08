"""Add the durable A8 purge command record.

Revision ID: 0009_purge_jobs
Revises: 0008_enrich_tags
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_purge_jobs"
down_revision: str | None = "0008_enrich_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purge_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("result_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("scope_kind IN ('grant', 'post')", name="ck_purge_jobs_scope_kind"),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'failed')", name="ck_purge_jobs_state"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purge_jobs"),
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM purge_jobs")).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: purge_jobs contains {count} command records; "
            "preserve them before downgrading"
        )
    op.drop_table("purge_jobs")
