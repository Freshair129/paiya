"""SQLAlchemy 2 Core mappings for the fixture-only A2 persistence slice.

Future domains can extend this metadata through separate, reviewed migrations.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
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
    Column("target_key", Text, nullable=False),
    Column("logical_key", Text, nullable=False, unique=True),
    Column("request_digest", Text, nullable=True),
    Column("due_at", DateTime(timezone=True), nullable=False),
    Column("state", Text, nullable=False),
    Column("lease_token", UUID(as_uuid=True), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("retry_count", Integer, nullable=False, server_default=text("0")),
    CheckConstraint(
        "kind IN ('discover', 'revisit', 'manual_import', 'enrich', 'purge')",
        name="ck_jobs_kind",
    ),
    CheckConstraint(
        "state IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')",
        name="ck_jobs_state",
    ),
    ForeignKeyConstraint(
        ["grant_id", "group_id"],
        ["source_grants.id", "source_grants.group_id"],
        name="fk_jobs_grant_scope",
    ),
)
Index(
    "ix_jobs_queued_due",
    jobs.c.due_at,
    jobs.c.id,
    postgresql_where=text("state = 'queued'"),
)
Index(
    "ix_jobs_leased_until",
    jobs.c.lease_until,
    postgresql_where=text("state = 'leased'"),
)
Index(
    "uq_jobs_active_revisit_target",
    jobs.c.target_key,
    unique=True,
    postgresql_where=text("kind = 'revisit' AND state IN ('queued', 'leased')"),
)

capture_runs = Table(
    "capture_runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("job_id", UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("group_id", UUID(as_uuid=True), nullable=False),
    Column("grant_id", UUID(as_uuid=True), nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=True),
    Column("session_result", Text, nullable=True),
    Column("sort_result", Text, nullable=True),
    Column("coverage", Text, nullable=True),
    Column("partial_reasons", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("stop_reason", Text, nullable=True),
    Column("cards_seen", Integer, nullable=True),
    Column("cards_parsed", Integer, nullable=True),
    Column("scroll_steps", Integer, nullable=True),
    Column("error_code", Text, nullable=True),
    CheckConstraint(
        "coverage IS NULL OR coverage IN ('complete', 'partial', 'none')",
        name="ck_runs_coverage",
    ),
    CheckConstraint(
        "stop_reason IS NULL OR stop_reason IN ('overlap_reached', 'feed_end', "
        "'time_budget_exhausted', 'scroll_budget_exhausted', 'access_blocked')",
        name="ck_runs_stop_reason",
    ),
    ForeignKeyConstraint(
        ["grant_id", "group_id"],
        ["source_grants.id", "source_grants.group_id"],
        name="fk_runs_grant_scope",
    ),
    UniqueConstraint("id", "group_id", "grant_id", name="uq_runs_scope"),
)

capture_gaps = Table(
    "capture_gaps",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("group_id", UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False),
    Column("gap_from", DateTime(timezone=True), nullable=False),
    Column("gap_to", DateTime(timezone=True), nullable=False),
    Column("reason", Text, nullable=False),
    Column("detected_at", DateTime(timezone=True), nullable=False),
    Column("compensated_by_job_id", UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=True),
    CheckConstraint("gap_to > gap_from", name="ck_capture_gaps_positive_range"),
    CheckConstraint("reason IN ('missed_slots', 'run_failed')", name="ck_capture_gaps_reason"),
)
Index("ix_capture_gaps_group_gap_from", capture_gaps.c.group_id, capture_gaps.c.gap_from)

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

watches = Table(
    "watches",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "post_id",
        UUID(as_uuid=True),
        ForeignKey("posts.id", name="fk_watches_post_id_posts_id"),
        nullable=False,
    ),
    Column("item_id", UUID(as_uuid=True), nullable=True),
    Column("saved_at", DateTime(timezone=True), nullable=False),
    Column("hot_until", DateTime(timezone=True), nullable=True),
    Column("next_due_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        ["item_id", "post_id"],
        ["listing_items.id", "listing_items.post_id"],
        name="fk_watches_item_post",
    ),
    UniqueConstraint(
        "post_id",
        "item_id",
        name="uq_watches_post_item",
        postgresql_nulls_not_distinct=True,
    ),
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
    UniqueConstraint("id", "observation_id", "post_id", name="uq_evidence_scope"),
)

price_facts = Table(
    "price_facts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("observation_id", UUID(as_uuid=True), nullable=False),
    Column("post_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=True),
    Column("evidence_id", UUID(as_uuid=True), nullable=False),
    Column("fact_key", Text, nullable=False),
    Column("price_kind", Text, nullable=False),
    Column("unit", Text, nullable=False),
    Column("amount", Numeric(14, 2), nullable=False),
    Column("amount_max", Numeric(14, 2), nullable=True),
    Column("currency", Text, nullable=False),
    ForeignKeyConstraint(
        ["observation_id", "post_id"],
        ["observations.id", "observations.post_id"],
        name="fk_price_facts_observation_post",
    ),
    ForeignKeyConstraint(
        ["item_id", "post_id"],
        ["listing_items.id", "listing_items.post_id"],
        name="fk_price_facts_item_post",
    ),
    ForeignKeyConstraint(
        ["evidence_id", "observation_id", "post_id"],
        ["evidence.id", "evidence.observation_id", "evidence.post_id"],
        name="fk_price_facts_evidence_scope",
    ),
    CheckConstraint(
        "amount >= 0 AND (amount_max IS NULL OR amount_max >= amount)",
        name="ck_price_facts_amount_range",
    ),
    UniqueConstraint("observation_id", "fact_key", name="uq_price_facts_observation_fact"),
    UniqueConstraint("id", "item_id", "post_id", name="uq_price_facts_id_item_post"),
)
Index(
    "ix_price_facts_item_kind_observation",
    price_facts.c.item_id,
    price_facts.c.price_kind,
    price_facts.c.observation_id,
)

status_facts = Table(
    "status_facts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("observation_id", UUID(as_uuid=True), nullable=False),
    Column("post_id", UUID(as_uuid=True), nullable=False),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("evidence_id", UUID(as_uuid=True), nullable=False),
    Column("fact_key", Text, nullable=False),
    Column("sale_status", Text, nullable=False),
    Column("effective_at", DateTime(timezone=True), nullable=True),
    Column("precision", Text, nullable=False),
    Column("rule_version", Text, nullable=False),
    Column("validation_result", Text, nullable=False),
    Column("reason", Text, nullable=True),
    ForeignKeyConstraint(
        ["observation_id", "post_id"],
        ["observations.id", "observations.post_id"],
        name="fk_status_facts_observation_post",
    ),
    ForeignKeyConstraint(
        ["item_id", "post_id"],
        ["listing_items.id", "listing_items.post_id"],
        name="fk_status_facts_item_post",
    ),
    ForeignKeyConstraint(
        ["evidence_id", "observation_id", "post_id"],
        ["evidence.id", "evidence.observation_id", "evidence.post_id"],
        name="fk_status_facts_evidence_scope",
    ),
    CheckConstraint(
        "sale_status IN ('unknown', 'open', 'reserved', 'sold', 'withdrawn')",
        name="ck_status_facts_sale_status",
    ),
    CheckConstraint(
        "validation_result IN ('confirmed', 'rejected')",
        name="ck_status_facts_validation_result",
    ),
    CheckConstraint(
        "(validation_result = 'confirmed' AND reason IS NULL) OR "
        "(validation_result = 'rejected' AND reason IS NOT NULL)",
        name="ck_status_facts_reason_required",
    ),
    CheckConstraint(
        "reason IS NULL OR reason IN "
        "('role_mismatch', 'actor_mismatch', 'seller_unknown', 'no_eligible_evidence')",
        name="ck_status_facts_reason",
    ),
    UniqueConstraint(
        "observation_id", "item_id", "fact_key", name="uq_status_facts_observation_item_fact"
    ),
    UniqueConstraint("id", "item_id", "post_id", name="uq_status_facts_id_item_post"),
)
Index(
    "ix_status_facts_item_effective",
    status_facts.c.item_id,
    status_facts.c.effective_at,
    status_facts.c.observation_id,
)

sale_events = Table(
    "sale_events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("item_id", UUID(as_uuid=True), nullable=False),
    Column("post_id", UUID(as_uuid=True), nullable=False),
    Column("status_fact_id", UUID(as_uuid=True), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("sale_status", Text, nullable=False),
    Column("cause_key", Text, nullable=False),
    Column("effective_at", DateTime(timezone=True), nullable=True),
    Column("precision", Text, nullable=False),
    ForeignKeyConstraint(
        ["item_id", "post_id"],
        ["listing_items.id", "listing_items.post_id"],
        name="fk_sale_events_item_post",
    ),
    ForeignKeyConstraint(
        ["status_fact_id", "item_id", "post_id"],
        ["status_facts.id", "status_facts.item_id", "status_facts.post_id"],
        name="fk_sale_events_status_fact_scope",
    ),
    CheckConstraint(
        "sale_status IN ('unknown', 'open', 'reserved', 'sold', 'withdrawn')",
        name="ck_sale_events_sale_status",
    ),
    CheckConstraint("seq > 0", name="ck_sale_events_seq_positive"),
    UniqueConstraint("item_id", "seq", name="uq_sale_events_item_seq"),
    UniqueConstraint("item_id", "cause_key", name="uq_sale_events_item_cause"),
    UniqueConstraint("id", "item_id", "post_id", name="uq_sale_events_id_item_post"),
)

current_items = Table(
    "current_items",
    metadata,
    Column("item_id", UUID(as_uuid=True), primary_key=True),
    # Keep post_id so every projection pointer can use a same-post composite FK.
    Column("post_id", UUID(as_uuid=True), nullable=False),
    Column("status_event_id", UUID(as_uuid=True), nullable=True),
    Column("status_fact_id", UUID(as_uuid=True), nullable=True),
    Column("ask_fact_id", UUID(as_uuid=True), nullable=True),
    Column("shipping_fact_id", UUID(as_uuid=True), nullable=True),
    Column("last_observation_id", UUID(as_uuid=True), nullable=False),
    Column("last_success_at", DateTime(timezone=True), nullable=True),
    Column("needs_review", Boolean, nullable=False, server_default=text("false")),
    ForeignKeyConstraint(
        ["item_id", "post_id"],
        ["listing_items.id", "listing_items.post_id"],
        name="fk_current_items_item_post",
    ),
    ForeignKeyConstraint(
        ["last_observation_id", "post_id"],
        ["observations.id", "observations.post_id"],
        name="fk_current_items_last_observation_post",
    ),
    ForeignKeyConstraint(
        ["status_event_id", "item_id", "post_id"],
        ["sale_events.id", "sale_events.item_id", "sale_events.post_id"],
        name="fk_current_items_status_event_scope",
    ),
    ForeignKeyConstraint(
        ["status_fact_id", "item_id", "post_id"],
        ["status_facts.id", "status_facts.item_id", "status_facts.post_id"],
        name="fk_current_items_status_fact_scope",
    ),
    ForeignKeyConstraint(
        ["ask_fact_id", "item_id", "post_id"],
        ["price_facts.id", "price_facts.item_id", "price_facts.post_id"],
        name="fk_current_items_ask_fact_scope",
    ),
    ForeignKeyConstraint(
        ["shipping_fact_id", "item_id", "post_id"],
        ["price_facts.id", "price_facts.item_id", "price_facts.post_id"],
        name="fk_current_items_shipping_fact_scope",
    ),
)
