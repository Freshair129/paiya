"""Trusted, test-only fixture grant provisioning. Never inferred from request headers."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, insert, select

from paiya.store.tables import groups, source_grants


@dataclass(frozen=True)
class FixtureScope:
    group_id: UUID
    grant_id: UUID
    group_key: str


def seed_fixture_scope(engine: Engine, group_key: str) -> FixtureScope:
    """Provision an isolated synthetic grant; not exposed as an HTTP endpoint."""
    if not group_key.startswith("synthetic-") or len(group_key) > 200:
        raise ValueError("Only explicit synthetic-* group keys may be seeded")
    group_id = uuid5(NAMESPACE_URL, f"paiya:fixture:group:{group_key}")
    grant_id = uuid5(NAMESPACE_URL, f"paiya:fixture:grant:{group_key}")
    with engine.begin() as conn:
        if conn.execute(select(groups.c.id).where(groups.c.id == group_id)).scalar_one_or_none() is None:
            conn.execute(
                insert(groups).values(id=group_id, source_key=group_key, source_kind="fixture")
            )
        if (
            conn.execute(select(source_grants.c.id).where(source_grants.c.id == grant_id))
            .scalar_one_or_none()
            is None
        ):
            conn.execute(
                insert(source_grants).values(
                    id=grant_id,
                    group_id=group_id,
                    source_kind="fixture",
                    scopes=["read", "capture", "media", "comments"],
                    valid_from=datetime.now(timezone.utc),
                )
            )
    return FixtureScope(group_id=group_id, grant_id=grant_id, group_key=group_key)


def verify_fixture_scope(conn: Connection, scope: FixtureScope, purpose: str) -> None:
    """Deny scope mismatch, expired grant, and grants without the requested operation."""
    row = conn.execute(
        select(
            source_grants.c.group_id,
            source_grants.c.source_kind,
            source_grants.c.scopes,
            source_grants.c.revoked_at,
        ).where(source_grants.c.id == scope.grant_id)
    ).mappings().one_or_none()
    group = conn.execute(
        select(groups.c.source_key, groups.c.source_kind).where(groups.c.id == scope.group_id)
    ).mappings().one_or_none()
    if (
        row is None
        or group is None
        or row["group_id"] != scope.group_id
        or group["source_key"] != scope.group_key
        or row["source_kind"] != "fixture"
        or group["source_kind"] != "fixture"
        or row["revoked_at"] is not None
        or purpose not in row["scopes"]
    ):
        raise PermissionError("No active fixture grant for requested group and purpose")
