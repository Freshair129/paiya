"""Trusted, server-selected read context for fixture scope checks."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Connection, select

from paiya.read.errors import GrantUnavailable
from paiya.store.fixture_scope import FixtureScope, verify_fixture_scope
from paiya.store.tables import groups, source_grants

ReadContext = FixtureScope
READ_CONTEXT_GROUP_KEY_ENV = "PAIYA_READ_CONTEXT_GROUP_KEY"
DEV_FIXTURE_MODE_ENV = "PAIYA_DEV_FIXTURE_MODE"


@dataclass(frozen=True, slots=True)
class ReadableGrant:
    """One server-resolved read grant available to the owner session."""

    group_id: UUID
    grant_id: UUID
    group_key: str
    group_source: str
    source_kind: str


ReadAccess = ReadContext | ReadableGrant | list[ReadableGrant]


def dev_fixture_mode_enabled() -> bool:
    """Return true only for the explicit local fixture opt-in."""
    value = os.environ.get(DEV_FIXTURE_MODE_ENV)
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    raise ValueError(f"{DEV_FIXTURE_MODE_ENV} must be '1' or '0'")


def read_context_group_key_from_env() -> str | None:
    """Read and validate the configured fixture group for existing local services."""
    group_key = os.environ.get(READ_CONTEXT_GROUP_KEY_ENV)
    if group_key is not None and not group_key.startswith("synthetic-"):
        raise ValueError(f"{READ_CONTEXT_GROUP_KEY_ENV} must start with 'synthetic-'")
    return group_key


def dev_fixture_group_key_from_env() -> str | None:
    """Return the configured fixture group only after the explicit HTTP dev opt-in."""
    group_key = read_context_group_key_from_env()
    return group_key if dev_fixture_mode_enabled() else None


def resolve_readable_grants(
    conn: Connection, *, now: datetime | None = None
) -> list[ReadableGrant]:
    """Resolve every currently active source grant that allows owner reads."""
    current = now or datetime.now(UTC)
    rows = (
        conn.execute(
            select(
                groups.c.id.label("group_id"),
                groups.c.group_key,
                groups.c.source.label("group_source"),
                source_grants.c.id.label("grant_id"),
                source_grants.c.source_kind,
            )
            .select_from(groups.join(source_grants, source_grants.c.group_id == groups.c.id))
            .where(
                source_grants.c.revoked_at.is_(None),
                source_grants.c.valid_from <= current,
                (source_grants.c.valid_until.is_(None) | (source_grants.c.valid_until > current)),
                source_grants.c.scopes.contains(["read"]),
            )
            .order_by(groups.c.id, source_grants.c.valid_from.desc(), source_grants.c.id)
        )
        .mappings()
        .all()
    )
    return [
        ReadableGrant(
            group_id=cast(UUID, row["group_id"]),
            grant_id=cast(UUID, row["grant_id"]),
            group_key=cast(str, row["group_key"]),
            group_source=cast(str, row["group_source"]),
            source_kind=cast(str, row["source_kind"]),
        )
        for row in rows
    ]


def resolve_read_context(conn: Connection, group_key: str) -> ReadContext:
    """Resolve the active read grant for one configured fixture group."""
    now = datetime.now(UTC)
    row = (
        conn.execute(
            select(groups.c.id.label("group_id"), source_grants.c.id.label("grant_id"))
            .select_from(groups.join(source_grants, source_grants.c.group_id == groups.c.id))
            .where(
                groups.c.source == "fixture",
                groups.c.group_key == group_key,
                source_grants.c.source_kind == "fixture",
                source_grants.c.revoked_at.is_(None),
                source_grants.c.valid_from <= now,
                (source_grants.c.valid_until.is_(None) | (source_grants.c.valid_until > now)),
                source_grants.c.scopes.contains(["read"]),
            )
            .order_by(source_grants.c.valid_from.desc(), source_grants.c.id)
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise GrantUnavailable()

    context = FixtureScope(
        group_id=cast(UUID, row["group_id"]),
        grant_id=cast(UUID, row["grant_id"]),
        group_key=group_key,
    )
    try:
        verify_fixture_scope(conn, context, "read")
    except PermissionError as error:
        raise GrantUnavailable() from error
    return context
