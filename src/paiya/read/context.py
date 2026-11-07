"""Trusted, server-selected read context for fixture scope checks."""

import os
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Connection, select

from paiya.read.errors import GrantUnavailable
from paiya.store.fixture_scope import FixtureScope, verify_fixture_scope
from paiya.store.tables import groups, source_grants

ReadContext = FixtureScope
READ_CONTEXT_GROUP_KEY_ENV = "PAIYA_READ_CONTEXT_GROUP_KEY"


def read_context_group_key_from_env() -> str | None:
    """Read and validate the optional server-side fixture group selection."""
    group_key = os.environ.get(READ_CONTEXT_GROUP_KEY_ENV)
    if group_key is not None and not group_key.startswith("synthetic-"):
        raise ValueError(f"{READ_CONTEXT_GROUP_KEY_ENV} must start with 'synthetic-'")
    return group_key


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
                source_grants.c.scopes.any("read"),
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
