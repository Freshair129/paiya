"""Add item price/status facts and rebuildable current projections.

Revision ID: 0004_item_status
Revises: 0003_discovery_queue
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_item_status"
down_revision: str | None = "0003_discovery_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_evidence_scope", "evidence", ["id", "observation_id", "post_id"]
    )

    op.create_table(
        "price_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_key", sa.Text(), nullable=False),
        sa.Column("price_kind", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount_max", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation_id", "post_id"],
            ["observations.id", "observations.post_id"],
            name="fk_price_facts_observation_post",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "post_id"],
            ["listing_items.id", "listing_items.post_id"],
            name="fk_price_facts_item_post",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "observation_id", "post_id"],
            ["evidence.id", "evidence.observation_id", "evidence.post_id"],
            name="fk_price_facts_evidence_scope",
        ),
        sa.CheckConstraint(
            "amount >= 0 AND (amount_max IS NULL OR amount_max >= amount)",
            name="ck_price_facts_amount_range",
        ),
        sa.UniqueConstraint("observation_id", "fact_key", name="uq_price_facts_observation_fact"),
        sa.UniqueConstraint("id", "item_id", "post_id", name="uq_price_facts_id_item_post"),
    )
    op.create_index(
        "ix_price_facts_item_kind_observation",
        "price_facts",
        ["item_id", "price_kind", "observation_id"],
    )

    op.create_table(
        "status_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_key", sa.Text(), nullable=False),
        sa.Column("sale_status", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("precision", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("validation_result", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["observation_id", "post_id"],
            ["observations.id", "observations.post_id"],
            name="fk_status_facts_observation_post",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "post_id"],
            ["listing_items.id", "listing_items.post_id"],
            name="fk_status_facts_item_post",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "observation_id", "post_id"],
            ["evidence.id", "evidence.observation_id", "evidence.post_id"],
            name="fk_status_facts_evidence_scope",
        ),
        sa.CheckConstraint(
            "sale_status IN ('unknown', 'open', 'reserved', 'sold', 'withdrawn')",
            name="ck_status_facts_sale_status",
        ),
        sa.CheckConstraint(
            "validation_result IN ('confirmed', 'rejected')",
            name="ck_status_facts_validation_result",
        ),
        sa.CheckConstraint(
            "(validation_result = 'confirmed' AND reason IS NULL) OR "
            "(validation_result = 'rejected' AND reason IS NOT NULL)",
            name="ck_status_facts_reason_required",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN "
            "('role_mismatch', 'actor_mismatch', 'seller_unknown', 'no_eligible_evidence')",
            name="ck_status_facts_reason",
        ),
        sa.UniqueConstraint(
            "observation_id", "item_id", "fact_key", name="uq_status_facts_observation_item_fact"
        ),
        sa.UniqueConstraint("id", "item_id", "post_id", name="uq_status_facts_id_item_post"),
    )
    op.create_index(
        "ix_status_facts_item_effective",
        "status_facts",
        ["item_id", "effective_at", "observation_id"],
    )

    op.create_table(
        "sale_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status_fact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("sale_status", sa.Text(), nullable=False),
        sa.Column("cause_key", sa.Text(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("precision", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id", "post_id"],
            ["listing_items.id", "listing_items.post_id"],
            name="fk_sale_events_item_post",
        ),
        sa.ForeignKeyConstraint(
            ["status_fact_id", "item_id", "post_id"],
            ["status_facts.id", "status_facts.item_id", "status_facts.post_id"],
            name="fk_sale_events_status_fact_scope",
        ),
        sa.CheckConstraint(
            "sale_status IN ('unknown', 'open', 'reserved', 'sold', 'withdrawn')",
            name="ck_sale_events_sale_status",
        ),
        sa.CheckConstraint("seq > 0", name="ck_sale_events_seq_positive"),
        sa.UniqueConstraint("item_id", "seq", name="uq_sale_events_item_seq"),
        sa.UniqueConstraint("item_id", "cause_key", name="uq_sale_events_item_cause"),
        sa.UniqueConstraint("id", "item_id", "post_id", name="uq_sale_events_id_item_post"),
    )

    op.create_table(
        "current_items",
        sa.Column("item_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status_fact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ask_fact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("shipping_fact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(
            ["item_id", "post_id"],
            ["listing_items.id", "listing_items.post_id"],
            name="fk_current_items_item_post",
        ),
        sa.ForeignKeyConstraint(
            ["last_observation_id", "post_id"],
            ["observations.id", "observations.post_id"],
            name="fk_current_items_last_observation_post",
        ),
        sa.ForeignKeyConstraint(
            ["status_event_id", "item_id", "post_id"],
            ["sale_events.id", "sale_events.item_id", "sale_events.post_id"],
            name="fk_current_items_status_event_scope",
        ),
        sa.ForeignKeyConstraint(
            ["status_fact_id", "item_id", "post_id"],
            ["status_facts.id", "status_facts.item_id", "status_facts.post_id"],
            name="fk_current_items_status_fact_scope",
        ),
        sa.ForeignKeyConstraint(
            ["ask_fact_id", "item_id", "post_id"],
            ["price_facts.id", "price_facts.item_id", "price_facts.post_id"],
            name="fk_current_items_ask_fact_scope",
        ),
        sa.ForeignKeyConstraint(
            ["shipping_fact_id", "item_id", "post_id"],
            ["price_facts.id", "price_facts.item_id", "price_facts.post_id"],
            name="fk_current_items_shipping_fact_scope",
        ),
    )


def downgrade() -> None:
    op.drop_table("current_items")
    op.drop_table("sale_events")
    op.drop_index("ix_status_facts_item_effective", table_name="status_facts")
    op.drop_table("status_facts")
    op.drop_index("ix_price_facts_item_kind_observation", table_name="price_facts")
    op.drop_table("price_facts")
    op.drop_constraint("uq_evidence_scope", "evidence", type_="unique")
