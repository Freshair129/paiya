"""Allow model-proposed enrichment tags and retain per-job summaries.

Revision ID: 0008_enrich_tags
Revises: 0007_tags
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_enrich_tags"
down_revision: str | None = "0007_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_TAG_REASONS = (
    "'quote_mismatch', 'role_mismatch', 'actor_mismatch', 'seller_unknown', "
    "'no_eligible_evidence', 'ambiguous_model', 'unknown_model', 'ambiguous_vram', "
    "'vram_not_valid_for_model', 'model_unknown', 'ambiguous_condition', "
    "'ambiguous_warranty', 'ambiguous_warranty_until', 'warranty_date_invalid'"
)
_ENRICH_TAG_REASONS = (
    _OLD_TAG_REASONS
    + ", 'unknown_reference', 'quote_not_found', 'value_out_of_vocabulary', 'rule_not_confirmed'"
)


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )

    op.drop_constraint("ck_tags_method", "tags", type_="check")
    op.create_check_constraint("ck_tags_method", "tags", "method IN ('rule', 'model')")

    op.drop_constraint("ck_tags_rule_versions", "tags", type_="check")
    op.create_check_constraint(
        "ck_tags_rule_versions",
        "tags",
        "(method = 'rule' AND model_version IS NULL AND confidence IS NULL) OR "
        "(method = 'model' AND model_version IS NOT NULL AND confidence IS NOT NULL "
        "AND confidence BETWEEN 0 AND 1)",
    )

    op.drop_constraint("ck_tags_validation_result", "tags", type_="check")
    op.create_check_constraint(
        "ck_tags_validation_result",
        "tags",
        "validation_result IN ('confirmed', 'proposed', 'rejected')",
    )

    op.drop_constraint("ck_tags_reason_required", "tags", type_="check")
    op.create_check_constraint(
        "ck_tags_reason_required",
        "tags",
        "(validation_result = 'confirmed' AND reason IS NULL) OR "
        "(validation_result IN ('proposed', 'rejected') AND reason IS NOT NULL)",
    )

    op.drop_constraint("ck_tags_reason", "tags", type_="check")
    op.create_check_constraint(
        "ck_tags_reason", "tags", f"reason IS NULL OR reason IN ({_ENRICH_TAG_REASONS})"
    )


def downgrade() -> None:
    tag_count, result_count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM tags "
                "WHERE method = 'model' OR validation_result = 'proposed'), "
                "(SELECT count(*) FROM jobs WHERE result IS NOT NULL)"
            )
        )
        .one()
    )
    if tag_count or result_count:
        raise RuntimeError(
            f"Cannot downgrade: {tag_count} incompatible tags, {result_count} jobs with result; "
            "remove those values deliberately before downgrading"
        )

    op.drop_constraint("ck_tags_reason", "tags", type_="check")
    op.create_check_constraint(
        "ck_tags_reason", "tags", f"reason IS NULL OR reason IN ({_OLD_TAG_REASONS})"
    )

    op.drop_constraint("ck_tags_reason_required", "tags", type_="check")
    op.create_check_constraint(
        "ck_tags_reason_required",
        "tags",
        "(validation_result = 'confirmed' AND reason IS NULL) OR "
        "(validation_result = 'rejected' AND reason IS NOT NULL)",
    )

    op.drop_constraint("ck_tags_validation_result", "tags", type_="check")
    op.create_check_constraint(
        "ck_tags_validation_result", "tags", "validation_result IN ('confirmed', 'rejected')"
    )

    op.drop_constraint("ck_tags_rule_versions", "tags", type_="check")
    op.create_check_constraint(
        "ck_tags_rule_versions", "tags", "model_version IS NULL AND confidence IS NULL"
    )

    op.drop_constraint("ck_tags_method", "tags", type_="check")
    op.create_check_constraint("ck_tags_method", "tags", "method = 'rule'")

    op.drop_column("jobs", "result")
