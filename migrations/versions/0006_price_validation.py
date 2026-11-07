"""Validate price facts from their stored evidence.

Revision ID: 0006_price_validation
Revises: 0005_watches
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_price_validation"
down_revision: str | None = "0005_watches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("price_facts", sa.Column("rule_version", sa.Text(), nullable=True))
    op.add_column("price_facts", sa.Column("validation_result", sa.Text(), nullable=True))
    op.add_column("price_facts", sa.Column("reason", sa.Text(), nullable=True))

    op.execute(
        r"""
        DO $$
        DECLARE
            fact_row record;
            evidence_key text;
            evidence_keys jsonb;
            evidence_row record;
            candidate_reason text;
            first_reason text;
            confirmed boolean;
        BEGIN
            FOR fact_row IN
                SELECT pf.id,
                       pf.observation_id,
                       pf.evidence_id,
                       li.source_item_key,
                       o.normalized_json,
                       seller.external_actor_key AS seller_actor_key,
                       first_evidence.local_key AS first_local_key
                FROM price_facts AS pf
                JOIN observations AS o ON o.id = pf.observation_id
                JOIN evidence AS first_evidence ON first_evidence.id = pf.evidence_id
                LEFT JOIN listing_items AS li ON li.id = pf.item_id
                LEFT JOIN posts AS post ON post.id = pf.post_id
                LEFT JOIN sellers AS seller ON seller.id = post.seller_id
                ORDER BY pf.id
            LOOP
                evidence_keys := NULL;
                IF fact_row.source_item_key IS NOT NULL THEN
                    SELECT item.value -> 'evidence_keys'
                    INTO evidence_keys
                    FROM jsonb_array_elements(
                        COALESCE(
                            fact_row.normalized_json -> 'payload' -> 'items',
                            '[]'::jsonb
                        )
                    ) AS item(value)
                    WHERE item.value ->> 'source_item_key' = fact_row.source_item_key
                    LIMIT 1;
                END IF;

                IF evidence_keys IS NULL THEN
                    evidence_keys := jsonb_build_array(fact_row.first_local_key);
                ELSIF jsonb_typeof(evidence_keys) <> 'array' THEN
                    evidence_keys := jsonb_build_array(fact_row.first_local_key);
                ELSIF jsonb_array_length(evidence_keys) = 0 THEN
                    evidence_keys := jsonb_build_array(fact_row.first_local_key);
                END IF;

                confirmed := false;
                first_reason := NULL;
                FOR evidence_key IN SELECT jsonb_array_elements_text(evidence_keys)
                LOOP
                    SELECT ev.role,
                           ev.source_span,
                           ev.comment_version_id,
                           version.role AS comment_role,
                           version.actor_key AS comment_actor_key
                    INTO evidence_row
                    FROM evidence AS ev
                    LEFT JOIN comment_versions AS version
                      ON version.id = ev.comment_version_id
                    WHERE ev.observation_id = fact_row.observation_id
                      AND ev.local_key = evidence_key
                    LIMIT 1;

                    IF NOT FOUND THEN
                        CONTINUE;
                    END IF;

                    IF evidence_row.role = 'source_label'
                       OR (
                           evidence_row.role = 'seller'
                           AND evidence_row.source_span ->> 'field' = 'post_text'
                       ) THEN
                        confirmed := true;
                        EXIT;
                    END IF;

                    IF evidence_row.source_span ->> 'field' = 'comment_text' THEN
                        IF evidence_row.comment_version_id IS NULL
                           OR evidence_row.comment_role IS NULL THEN
                            candidate_reason := 'no_eligible_evidence';
                        ELSIF evidence_row.role IS DISTINCT FROM evidence_row.comment_role THEN
                            candidate_reason := 'role_mismatch';
                        ELSIF fact_row.seller_actor_key IS NULL THEN
                            candidate_reason := 'seller_unknown';
                        ELSIF evidence_row.comment_actor_key
                              IS DISTINCT FROM fact_row.seller_actor_key THEN
                            candidate_reason := 'actor_mismatch';
                        ELSIF evidence_row.role = 'seller'
                              AND evidence_row.comment_role = 'seller' THEN
                            confirmed := true;
                            EXIT;
                        ELSE
                            candidate_reason := 'no_eligible_evidence';
                        END IF;
                    ELSE
                        candidate_reason := 'no_eligible_evidence';
                    END IF;

                    IF first_reason IS NULL THEN
                        first_reason := candidate_reason;
                    END IF;
                END LOOP;

                UPDATE price_facts
                SET rule_version = 'price-v1.0',
                    validation_result = CASE WHEN confirmed THEN 'confirmed' ELSE 'rejected' END,
                    reason = CASE
                        WHEN confirmed THEN NULL
                        ELSE COALESCE(first_reason, 'no_eligible_evidence')
                    END
                WHERE id = fact_row.id;
            END LOOP;
        END
        $$;
        """
    )

    op.alter_column("price_facts", "rule_version", nullable=False)
    op.alter_column("price_facts", "validation_result", nullable=False)
    op.create_check_constraint(
        "ck_price_facts_validation_result",
        "price_facts",
        "validation_result IN ('confirmed', 'rejected')",
    )
    op.create_check_constraint(
        "ck_price_facts_reason_required",
        "price_facts",
        "(validation_result = 'confirmed' AND reason IS NULL) OR "
        "(validation_result = 'rejected' AND reason IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_price_facts_reason",
        "price_facts",
        "reason IS NULL OR reason IN "
        "('role_mismatch', 'actor_mismatch', 'seller_unknown', 'no_eligible_evidence')",
    )
    op.create_index(
        "ix_price_facts_confirmed_ask_item_observation",
        "price_facts",
        ["item_id", "observation_id"],
        postgresql_where=sa.text("price_kind = 'ask' AND validation_result = 'confirmed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_price_facts_confirmed_ask_item_observation", table_name="price_facts")
    op.drop_constraint("ck_price_facts_reason", "price_facts", type_="check")
    op.drop_constraint("ck_price_facts_reason_required", "price_facts", type_="check")
    op.drop_constraint("ck_price_facts_validation_result", "price_facts", type_="check")
    op.drop_column("price_facts", "reason")
    op.drop_column("price_facts", "validation_result")
    op.drop_column("price_facts", "rule_version")
