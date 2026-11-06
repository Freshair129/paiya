"""SQLAlchemy 2 Core mappings for the fixture-only A2 persistence slice.

Future domains can extend this metadata through separate, reviewed migrations.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = MetaData()

groups = Table(
    "groups",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source_key", Text, nullable=False, unique=True),
    Column("source_kind", Text, nullable=False),
)

source_grants = Table(
    "source_grants",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("group_id", UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("scopes", ARRAY(Text), nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
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
    Column("job_kind", Text, nullable=False),
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
    Column("source_kind", Text, nullable=False),
    Column("identity_scope", Text, nullable=False),
    Column("external_actor_key", Text, nullable=False),
    Column("display_name", Text, nullable=True),
    UniqueConstraint(
        "source_kind",
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
    UniqueConstraint("group_id", "source_post_key", name="uq_posts_source"),
    UniqueConstraint("id", "group_id", name="uq_posts_group"),
)

listing_items = Table(
    "listing_items",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("post_id", UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False),
    Column("source_item_key", Text, nullable=False),
    Column("model", Text, nullable=True),
    Column("condition_text", Text, nullable=True),
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
    Column("access", Text, nullable=False),
    Column("activity", JSONB, nullable=True),
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
    UniqueConstraint("id", "post_id", "grant_id", name="uq_observations_evidence_scope"),
)

comments = Table(
    "comments",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("post_id", UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False),
    Column("source_comment_key", Text, nullable=False),
    UniqueConstraint("post_id", "source_comment_key", name="uq_comment_source"),
)

comment_versions = Table(
    "comment_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("comment_id", UUID(as_uuid=True), ForeignKey("comments.id"), nullable=False),
    Column("observation_id", UUID(as_uuid=True), ForeignKey("observations.id"), nullable=False),
    Column("role", Text, nullable=False),
    Column("actor_key", Text, nullable=True),
    Column("body", Text, nullable=False),
    Column("source_time", JSONB, nullable=False),
    UniqueConstraint("comment_id", "observation_id", name="uq_comment_version_observation"),
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
    Column("comment_version_id", UUID(as_uuid=True), ForeignKey("comment_versions.id")),
    Column("media_asset_id", UUID(as_uuid=True), ForeignKey("media_assets.id")),
    Column("local_key", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("source_span", JSONB, nullable=True),
    Column("media_state", Text, nullable=False),
    Column("media_reason", Text, nullable=True),
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
    UniqueConstraint("observation_id", "local_key", name="uq_evidence_local_key"),
)
