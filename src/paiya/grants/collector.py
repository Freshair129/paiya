"""Local grant policy approved for the first Collector group."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from paiya.grants.errors import GrantError
from paiya.store.tables import groups, source_grants

GROUP_KEY = "owner-group-01"
SOURCE_KIND = "facebook_live"
SCOPES = ("capture", "read", "media")
RETENTION_DAYS = 7
RETENTION = timedelta(days=RETENTION_DAYS)


def create_collector_grant(engine: Engine, authority_ref: str) -> dict[str, object]:
    authority_ref = authority_ref.strip()
    if not authority_ref or len(authority_ref) > 500:
        raise GrantError("invalid_authority_ref", "Supply a local owner decision reference")
    grant_id = uuid4()
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            pg_insert(groups)
            .values(
                id=uuid4(),
                source=SOURCE_KIND,
                group_key=GROUP_KEY,
                canonical_url=f"collector://{GROUP_KEY}",
                enabled=False,
            )
            .on_conflict_do_nothing(index_elements=[groups.c.source, groups.c.group_key])
        )
        group_id: UUID = conn.execute(
            select(groups.c.id)
            .where(groups.c.source == SOURCE_KIND, groups.c.group_key == GROUP_KEY)
            .with_for_update()
        ).scalar_one()
        conn.execute(
            insert(source_grants).values(
                id=grant_id,
                group_id=group_id,
                source_kind=SOURCE_KIND,
                scopes=list(SCOPES),
                authority_ref=authority_ref,
                valid_from=now,
                retention_days=RETENTION_DAYS,
            )
        )
    return {"group_key": GROUP_KEY, "grant_id": str(grant_id), "retention_days": RETENTION_DAYS}


def verify_collector_grant(
    conn: Connection,
    grant_id: UUID,
    group_key: str,
    started_at: datetime,
    *,
    lock: bool = False,
) -> UUID:
    query = (
        select(source_grants, groups.c.group_key, groups.c.source)
        .join(groups, source_grants.c.group_id == groups.c.id)
        .where(source_grants.c.id == grant_id)
    )
    if lock:
        query = query.with_for_update(of=source_grants, read=True)
    row = conn.execute(query).mappings().one_or_none()
    now = datetime.now(UTC)
    if (
        row is None
        or group_key != GROUP_KEY
        or row["group_key"] != group_key
        or row["source"] != SOURCE_KIND
        or row["source_kind"] != SOURCE_KIND
        or row["revoked_at"] is not None
        or row["valid_from"] > min(started_at, now)
        or (row["valid_until"] is not None and row["valid_until"] <= now)
        or row["retention_days"] != RETENTION_DAYS
        or not set(SCOPES).issubset(cast(list[str], row["scopes"]))
    ):
        raise GrantError("grant_unavailable", "No active Collector grant covers this run")
    return cast(UUID, row["group_id"])


def is_collector_scope(source: object, group_key: object) -> bool:
    return source == SOURCE_KIND and group_key == GROUP_KEY
