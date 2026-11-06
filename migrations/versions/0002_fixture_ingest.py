"""Fixture-only ingest schema. Frozen at revision 0002_fixture_ingest.

Revision ID: 0002_fixture_ingest
Revises: 0001_initial_empty
"""

from collections.abc import Sequence

from alembic import op

# Intentionally snapshot the table definitions in this revision.
# Importing evolving application metadata would change old migrations later.
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = MetaData()

groups = Table(
    "groups",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source", Text, nullable=False),
    Column("group_key", Text, nullable=False),
    Column("canonical_url", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("poll_minutes", SmallInteger, nullable=True),
    Column("discovery_checkpoint", JSONB, nullable=True),
    UniqueConstraint("source", "group_key", name="uq_groups_source_group_key"),
)

source_grants = Table(
    "source_grants",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("group_id", UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("scopes", ARRAY(Text), nullable=False),
    Column("authority_ref", Text, nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("retention_days", Integer, nullable=False),
    CheckConstraint("retention_days > 0", name="ck_source_grants_retention_days"),
    UniqueConstraint("id", "group_id", name="uq_grants_id_group"),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("group_id", UUID(as_uuid=True), nullable=False),
    Column("grant_id", UUID(as_uuid=True), nullable=False),
    Column("kind", Text, nullable=False),
    Column("logical_key", Text, nullable=False, unique=True),
    ForeignKeyConstraint(
        ["grant_id", "group_id"],
        ["source_grants.id", "source_grants.group_id"],
        name="fk_jobs_grant_scope",
    ),
)

capture_runs = Table(
    "capture_runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("job_id", UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("group_id", UUID(as_uuid=True), nullable=False),
    Column("grant_id", UUID(as_uuid=True), nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("session_result", Text, nullable=True),
    Column("sort_result", Text, nullable=True),
    ForeignKeyConstraint(
        ["grant_id", "group_id"],
        ["source_grants.id", "source_grants.group_id"],
        name="fk_runs_grant_scope",
    ),
    UniqueConstraint("id", "group_id", "grant_id", name="uq_runs_scope"),
)

sellers = Table(
    "sellers",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source", Text, nullable=False),
    Column("identity_scope", Text, nullable=False),
    Column("external_actor_key", Text, nullable=False),
    UniqueConstraint(
        "source",
        "identity_scope",
        "external_actor_key",
        name="uq_seller_stable_identity",
    ),
)

posts = Table(
    "posts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("group_id", UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False),
    Column("source_post_key", Text, nullable=False),
    Column("permalink", Text, nullable=False),
    Column("seller_id", UUID(as_uuid=True), ForeignKey("sellers.id"), nullable=True),
    Column("posted_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("group_id", "source_post_key", name="uq_posts_source"),
    UniqueConstraint("id", "group_id", name="uq_posts_group"),
)

listing_items = Table(
    "listing_items",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("post_id", UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False),
    Column("source_item_key", Text, nullable=True),
    Column("model_key", Text, nullable=True),
    Column("variant", Text, nullable=True),
    Column("vram_gb", SmallInteger, nullable=True),
    Column("condition", Text, nullable=True),
    Column("unit", Text, nullable=False),
    Column("resolution", Text, nullable=False),
    CheckConstraint("resolution IN ('keyed', 'unresolved', 'bundle')", name="ck_items_resolution"),
    UniqueConstraint("post_id", "source_item_key", name="uq_items_source"),
    UniqueConstraint("id", "post_id", name="uq_items_post"),
)

observations = Table(
    "observations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("post_id", UUID(as_uuid=True), nullable=False),
    Column("group_id", UUID(as_uuid=True), nullable=False),
    Column("grant_id", UUID(as_uuid=True), nullable=False),
    Column("capture_id", Text, nullable=False, unique=True),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("source_time", DateTime(timezone=True), nullable=True),
    Column("source_time_precision", Text, nullable=False),
    Column("access", Text, nullable=False),
    Column("coverage", Text, nullable=True),
    Column("parser_version", Text, nullable=False),
    Column("source_reported_comment_count", Integer, nullable=True),
    Column("distinct_comments_seen", Integer, nullable=True),
    Column("distinct_known_commenters_seen", Integer, nullable=True),
    Column("payload_hash", String(64), nullable=False),
    Column("normalized_json", JSONB, nullable=False),
    ForeignKeyConstraint(
        ["run_id", "group_id", "grant_id"],
        ["capture_runs.id", "capture_runs.group_id", "capture_runs.grant_id"],
        name="fk_observations_run_scope",
    ),
    ForeignKeyConstraint(
        ["post_id", "group_id"],
        ["posts.id", "posts.group_id"],
        name="fk_observations_post_scope",
    ),
    ForeignKeyConstraint(
        ["grant_id", "group_id"],
        ["source_grants.id", "source_grants.group_id"],
        name="fk_observations_grant_scope",
    ),
    UniqueConstraint("run_id", "post_id", name="uq_observations_run_post"),
    UniqueConstraint("id", "post_id", name="uq_observations_id_post"),
    UniqueConstraint("id", "post_id", "grant_id", name="uq_observations_evidence_scope"),
)

comments = Table(
    "comments",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("post_id", UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False),
    Column("source_comment_key", Text, nullable=True),
    Column("parent_id", UUID(as_uuid=True), nullable=True),
    ForeignKeyConstraint(
        ["parent_id", "post_id"],
        ["comments.id", "comments.post_id"],
        name="fk_comments_parent_post",
    ),
    UniqueConstraint("post_id", "source_comment_key", name="uq_comment_source"),
    UniqueConstraint("id", "post_id", name="uq_comments_id_post"),
)

comment_versions = Table(
    "comment_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("comment_id", UUID(as_uuid=True), nullable=False),
    Column("observation_id", UUID(as_uuid=True), nullable=False),
    Column("post_id", UUID(as_uuid=True), nullable=False),
    Column("role", Text, nullable=False),
    Column("actor_key", Text, nullable=True),
    Column("body", Text, nullable=False),
    Column("source_time", DateTime(timezone=True), nullable=True),
    Column("body_hash", String(64), nullable=False),
    ForeignKeyConstraint(
        ["comment_id", "post_id"],
        ["comments.id", "comments.post_id"],
        name="fk_comment_versions_comment_post",
    ),
    ForeignKeyConstraint(
        ["observation_id", "post_id"],
        ["observations.id", "observations.post_id"],
        name="fk_comment_versions_observation_post",
    ),
    UniqueConstraint("comment_id", "observation_id", name="uq_comment_version_observation"),
    UniqueConstraint("id", "observation_id", name="uq_comment_versions_id_observation"),
)

media_assets = Table(
    "media_assets",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("storage_key", Text, nullable=False, unique=True),
    Column("sha256", String(64), nullable=False, unique=True),
    Column("mime", Text, nullable=False),
    Column("bytes", BigInteger, nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    CheckConstraint("bytes > 0 AND width > 0 AND height > 0", name="ck_media_dimensions"),
)

evidence = Table(
    "evidence",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("observation_id", UUID(as_uuid=True), nullable=False),
    Column("post_id", UUID(as_uuid=True), nullable=False),
    Column("grant_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=True),
    Column("comment_version_id", UUID(as_uuid=True), nullable=True),
    Column("media_asset_id", UUID(as_uuid=True), ForeignKey("media_assets.id"), nullable=True),
    Column("local_key", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("source_span", JSONB, nullable=True),
    Column("media_state", Text, nullable=False),
    Column("media_reason", Text, nullable=True),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["observation_id", "post_id", "grant_id"],
        ["observations.id", "observations.post_id", "observations.grant_id"],
        name="fk_evidence_observation_scope",
    ),
    ForeignKeyConstraint(
        ["item_id", "post_id"],
        ["listing_items.id", "listing_items.post_id"],
        name="fk_evidence_item_post",
    ),
    ForeignKeyConstraint(
        ["comment_version_id", "observation_id"],
        ["comment_versions.id", "comment_versions.observation_id"],
        name="fk_evidence_comment_version_observation",
    ),
    CheckConstraint(
        "(media_state = 'captured') = (media_asset_id IS NOT NULL)",
        name="ck_evidence_captured_has_asset",
    ),
    UniqueConstraint("observation_id", "local_key", name="uq_evidence_local_key"),
)


revision: str = "0002_fixture_ingest"
down_revision: str | None = "0001_initial_empty"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in metadata.sorted_tables:
        table.create(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    for table in reversed(metadata.sorted_tables):
        table.drop(op.get_bind(), checkfirst=False)
