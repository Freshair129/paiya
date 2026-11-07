"""Store evidence-backed A6a extraction tags.

Revision ID: 0007_tags
Revises: 0006_price_validation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_tags"
down_revision: str | None = "0006_price_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listing_items", sa.Column("warranty", sa.Text(), nullable=True))
    op.add_column("listing_items", sa.Column("warranty_until", sa.Date(), nullable=True))
    op.create_check_constraint(
        "ck_items_warranty",
        "listing_items",
        "warranty IS NULL OR warranty IN ('active', 'none')",
    )
    op.create_check_constraint(
        "ck_items_warranty_until_first_day",
        "listing_items",
        "warranty_until IS NULL OR EXTRACT(DAY FROM warranty_until) = 1",
    )
    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("validation_result", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "post_id"],
            ["observations.id", "observations.post_id"],
            name="fk_tags_observation_post",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "post_id"],
            ["listing_items.id", "listing_items.post_id"],
            name="fk_tags_item_post",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "observation_id", "post_id"],
            ["evidence.id", "evidence.observation_id", "evidence.post_id"],
            name="fk_tags_evidence_scope",
        ),
        sa.CheckConstraint(
            "key IN ('model_key', 'vram_gb', 'condition', 'warranty', 'warranty_until')",
            name="ck_tags_key",
        ),
        sa.CheckConstraint("method = 'rule'", name="ck_tags_method"),
        sa.CheckConstraint(
            "model_version IS NULL AND confidence IS NULL", name="ck_tags_rule_versions"
        ),
        sa.CheckConstraint(
            "validation_result IN ('confirmed', 'rejected')",
            name="ck_tags_validation_result",
        ),
        sa.CheckConstraint(
            "(validation_result = 'confirmed' AND reason IS NULL) OR "
            "(validation_result = 'rejected' AND reason IS NOT NULL)",
            name="ck_tags_reason_required",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN "
            "('quote_mismatch', 'role_mismatch', 'actor_mismatch', 'seller_unknown', "
            "'no_eligible_evidence', 'ambiguous_model', 'unknown_model', 'ambiguous_vram', "
            "'vram_not_valid_for_model', 'model_unknown', 'ambiguous_condition', "
            "'ambiguous_warranty', 'ambiguous_warranty_until', 'warranty_date_invalid')",
            name="ck_tags_reason",
        ),
        sa.CheckConstraint(
            "validation_result <> 'confirmed' OR value IS NOT NULL",
            name="ck_tags_confirmed_value",
        ),
        sa.UniqueConstraint(
            "observation_id",
            "item_id",
            "key",
            "method",
            "rule_version",
            name="uq_tags_observation_item_key_method_version",
        ),
    )
    op.create_index(
        "ix_tags_item_key_observation",
        "tags",
        ["item_id", "key", "observation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tags_item_key_observation", table_name="tags")
    op.drop_table("tags")
    op.drop_constraint("ck_items_warranty_until_first_day", "listing_items", type_="check")
    op.drop_constraint("ck_items_warranty", "listing_items", type_="check")
    op.drop_column("listing_items", "warranty_until")
    op.drop_column("listing_items", "warranty")
