"""Pure connection-based group operations for the fixture-only A3 phase."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Connection, func, select, text, update

from paiya.groups.errors import GroupError
from paiya.store.fixture_scope import seed_fixture_scope_in_transaction
from paiya.store.tables import groups, jobs, source_grants
from paiya.store.transactions import atomic

GROUP_LIMIT = 5
INTERVALS = frozenset({30, 60})
_CAPACITY_LOCK = 0x50414959


def _check_now(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")


def _check_interval(poll_minutes: int) -> None:
    if poll_minutes not in INTERVALS:
        raise GroupError("invalid_interval", "poll_minutes must be 30 or 60")


def _lock_capacity(conn: Connection) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": _CAPACITY_LOCK})


def _enabled_count(conn: Connection) -> int:
    return cast(
        int,
        conn.execute(
            select(func.count())
            .select_from(groups)
            .where(groups.c.enabled.is_(True), groups.c.poll_minutes.is_not(None))
        ).scalar_one(),
    )


def _group_row(conn: Connection, group_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    query = select(groups).where(groups.c.id == group_id)
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().one_or_none()
    if row is None:
        raise GroupError("not_found", "Group was not found")
    return dict(row)


def _active_grant(conn: Connection, group_id: UUID, now: datetime) -> UUID | None:
    rows = (
        conn.execute(
            select(source_grants.c.id, source_grants.c.scopes)
            .select_from(source_grants.join(groups, groups.c.id == source_grants.c.group_id))
            .where(
                source_grants.c.group_id == group_id,
                groups.c.source == "fixture",
                source_grants.c.source_kind == "fixture",
                source_grants.c.revoked_at.is_(None),
                source_grants.c.valid_from <= now,
                (source_grants.c.valid_until.is_(None) | (source_grants.c.valid_until > now)),
            )
            .order_by(source_grants.c.valid_from.desc(), source_grants.c.id)
        )
        .mappings()
        .all()
    )
    for row in rows:
        if "capture" in row["scopes"]:
            return cast(UUID, row["id"])
    return None


def _cancel_queued(conn: Connection, group_id: UUID) -> None:
    conn.execute(
        update(jobs)
        .where(jobs.c.group_id == group_id, jobs.c.state == "queued")
        .values(state="cancelled")
    )


def _public_group(row: dict[str, Any], grant_available: bool) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "source": row["source"],
        "group_key": row["group_key"],
        "canonical_url": row["canonical_url"],
        "enabled": row["enabled"],
        "poll_minutes": row["poll_minutes"],
        "discovery_checkpoint": row["discovery_checkpoint"],
        "grant_available": grant_available,
    }


def add_group(
    conn: Connection, now: datetime, group_key: str, poll_minutes: int
) -> dict[str, Any]:
    """Add or activate a synthetic fixture group with a capture grant."""
    _check_now(now)
    _check_interval(poll_minutes)
    if not group_key.startswith("synthetic-") or len(group_key) > 200:
        raise GroupError("invalid_group_key", "Only synthetic-* fixture group keys are allowed")

    with atomic(conn):
        _lock_capacity(conn)
        existing = (
            conn.execute(
                select(groups).where(groups.c.source == "fixture", groups.c.group_key == group_key)
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None and existing["poll_minutes"] is not None:
            raise GroupError("group_already_exists", "Group already has a polling interval")

        active_count = _enabled_count(conn)
        if active_count >= GROUP_LIMIT:
            raise GroupError("group_limit_reached", "At most five polling groups may be enabled")

        scope = seed_fixture_scope_in_transaction(conn, group_key, now)
        if _active_grant(conn, scope.group_id, now) is None:
            raise GroupError("grant_unavailable", "An active fixture capture grant is required")
        conn.execute(
            update(groups)
            .where(groups.c.id == scope.group_id)
            .values(enabled=True, poll_minutes=poll_minutes)
        )
        return _public_group(_group_row(conn, scope.group_id), True)


def set_interval(
    conn: Connection, now: datetime, group_id: UUID, poll_minutes: int
) -> dict[str, Any]:
    """Change the polling interval and cancel queued jobs atomically."""
    _check_now(now)
    _check_interval(poll_minutes)
    with atomic(conn):
        _lock_capacity(conn)
        row = _group_row(conn, group_id, lock=True)
        if row["source"] != "fixture":
            raise GroupError("invalid_group", "Only fixture groups can be scheduled in phase A")
        if row["enabled"] and row["poll_minutes"] is None and _enabled_count(conn) >= GROUP_LIMIT:
            raise GroupError("group_limit_reached", "At most five polling groups may be enabled")
        _cancel_queued(conn, group_id)
        conn.execute(
            update(groups).where(groups.c.id == group_id).values(poll_minutes=poll_minutes)
        )
        updated = _group_row(conn, group_id)
        return _public_group(updated, _active_grant(conn, group_id, now) is not None)


def pause(conn: Connection, now: datetime, group_id: UUID) -> dict[str, Any]:
    """Pause a group and cancel its queued jobs in the same transaction."""
    _check_now(now)
    with atomic(conn):
        row = _group_row(conn, group_id, lock=True)
        if row["source"] != "fixture":
            raise GroupError("invalid_group", "Only fixture groups can be paused in phase A")
        _cancel_queued(conn, group_id)
        conn.execute(update(groups).where(groups.c.id == group_id).values(enabled=False))
        updated = _group_row(conn, group_id)
        return _public_group(updated, _active_grant(conn, group_id, now) is not None)


def resume(conn: Connection, now: datetime, group_id: UUID) -> dict[str, Any]:
    """Re-enable a group and queue its latest slot when its grant is active."""
    _check_now(now)
    with atomic(conn):
        _lock_capacity(conn)
        row = _group_row(conn, group_id, lock=True)
        if row["source"] != "fixture" or row["poll_minutes"] not in INTERVALS:
            raise GroupError(
                "invalid_group", "Only fixture groups with a 30 or 60 minute interval can resume"
            )
        if _active_grant(conn, group_id, now) is None:
            raise GroupError("grant_unavailable", "An active fixture capture grant is required")
        if not row["enabled"] and _enabled_count(conn) >= GROUP_LIMIT:
            raise GroupError("group_limit_reached", "At most five polling groups may be enabled")
        conn.execute(update(groups).where(groups.c.id == group_id).values(enabled=True))
        from paiya.scheduler.queue import schedule_group_due

        schedule_group_due(conn, now, group_id)
        updated = _group_row(conn, group_id)
        return _public_group(updated, True)


def list_groups(conn: Connection, now: datetime) -> list[dict[str, Any]]:
    """List fixture and future-source groups with their current capture grant state."""
    _check_now(now)
    rows = (
        conn.execute(
            select(groups)
            .where(groups.c.source == "fixture")
            .order_by(groups.c.group_key, groups.c.id)
        )
        .mappings()
        .all()
    )
    return [
        _public_group(dict(row), _active_grant(conn, cast(UUID, row["id"]), now) is not None)
        for row in rows
    ]
