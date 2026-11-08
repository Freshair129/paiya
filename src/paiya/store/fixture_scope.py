"""Trusted, test-only fixture grant provisioning. Never inferred from request headers."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Connection, Engine, insert, select

from paiya.store.tables import groups, source_grants


class FixtureGrantUnavailable(PermissionError):
    """A fixture grant is missing, inactive, revoked, or insufficient."""

    code = "grant_unavailable"


@dataclass(frozen=True)
class FixtureScope:
    group_id: UUID
    grant_id: UUID
    group_key: str


def seed_fixture_scope(engine: Engine, group_key: str) -> FixtureScope:
    """Provision an isolated synthetic grant; not exposed as an HTTP endpoint."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        return seed_fixture_scope_in_transaction(conn, group_key, now)


def seed_fixture_scope_in_transaction(
    conn: Connection, group_key: str, now: datetime
) -> FixtureScope:
    """Seed the A3 fixture grant on a caller-owned transaction and clock."""
    if not group_key.startswith("synthetic-") or len(group_key) > 200:
        raise ValueError("Only explicit synthetic-* group keys may be seeded")
    group_id = uuid5(NAMESPACE_URL, f"paiya:fixture:group:{group_key}")
    grant_id = uuid5(NAMESPACE_URL, f"paiya:fixture:grant:{group_key}")
    exists = conn.execute(select(groups.c.id).where(groups.c.id == group_id)).scalar_one_or_none()
    if exists is None:
        conn.execute(
            insert(groups).values(
                id=group_id,
                source="fixture",
                group_key=group_key,
                canonical_url=f"fixture://{group_key}",
            )
        )
    grant_exists = conn.execute(
        select(source_grants.c.id).where(source_grants.c.id == grant_id)
    ).scalar_one_or_none()
    if grant_exists is None:
        conn.execute(
            insert(source_grants).values(
                id=grant_id,
                group_id=group_id,
                source_kind="fixture",
                scopes=["read", "capture", "media", "comments"],
                authority_ref=f"synthetic-fixture-seed:{group_key}",
                valid_from=now,
                valid_until=None,
                retention_days=30,
            )
        )
    return FixtureScope(group_id=group_id, grant_id=grant_id, group_key=group_key)


def _verify_fixture_scope(
    conn: Connection, scope: FixtureScope, purpose: str, *, lock_for_share: bool
) -> None:
    group = (
        conn.execute(
            select(groups.c.source, groups.c.group_key).where(groups.c.id == scope.group_id)
        )
        .mappings()
        .one_or_none()
    )
    grant_query = select(
        source_grants.c.group_id,
        source_grants.c.source_kind,
        source_grants.c.scopes,
        source_grants.c.valid_from,
        source_grants.c.valid_until,
        source_grants.c.revoked_at,
    ).where(source_grants.c.id == scope.grant_id)
    if lock_for_share:
        grant_query = grant_query.with_for_update(read=True)
    row = conn.execute(grant_query).mappings().one_or_none()
    now = datetime.now(UTC)
    if (
        row is None
        or group is None
        or row["group_id"] != scope.group_id
        or group["group_key"] != scope.group_key
        or group["source"] != "fixture"
        or row["source_kind"] != "fixture"
        or row["revoked_at"] is not None
        or row["valid_from"] > now
        or (row["valid_until"] is not None and row["valid_until"] <= now)
        or purpose not in row["scopes"]
    ):
        raise FixtureGrantUnavailable("No active fixture grant for requested group and purpose")


def verify_fixture_scope(conn: Connection, scope: FixtureScope, purpose: str) -> None:
    """Deny a missing, expired, revoked, mismatched, or insufficient fixture grant."""
    _verify_fixture_scope(conn, scope, purpose, lock_for_share=False)


def verify_fixture_scope_for_commit(conn: Connection, scope: FixtureScope, purpose: str) -> None:
    """Recheck and hold the grant row with SELECT FOR SHARE through transaction commit."""
    _verify_fixture_scope(conn, scope, purpose, lock_for_share=True)
