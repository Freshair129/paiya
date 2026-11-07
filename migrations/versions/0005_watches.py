"""Add post watches and active revisit uniqueness.

Revision ID: 0005_watches
Revises: 0004_item_status
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_watches"
down_revision: str | None = "0004_item_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hot_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], name="fk_watches_post_id_posts_id"),
        sa.ForeignKeyConstraint(
            ["item_id", "post_id"],
            ["listing_items.id", "listing_items.post_id"],
            name="fk_watches_item_post",
        ),
        sa.UniqueConstraint(
            "post_id",
            "item_id",
            name="uq_watches_post_item",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "uq_jobs_active_revisit_target",
        "jobs",
        ["target_key"],
        unique=True,
        postgresql_where=sa.text("kind = 'revisit' AND state IN ('queued', 'leased')"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_active_revisit_target", table_name="jobs")
    op.drop_table("watches")
