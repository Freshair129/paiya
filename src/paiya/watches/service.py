"""Transactional post-level watch commands with an explicit caller clock."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, delete, func, or_, select, text, update

from paiya.store.tables import (
    current_items,
    groups,
    jobs,
    listing_items,
    observations,
    posts,
    sale_events,
    source_grants,
    watches,
)
from paiya.store.transactions import atomic
from paiya.watches.errors import WatchError

_WATCH_LOCK = 0x5041495941574154
HOT_WATCH_LIMIT = 10
HOT_DURATION = timedelta(hours=24)
REVISIT_INTERVAL = timedelta(hours=24)


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC)


def _lock_watches(conn: Connection) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": _WATCH_LOCK})


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
                or_(source_grants.c.valid_until.is_(None), source_grants.c.valid_until > now),
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


def _require_post(conn: Connection, post_id: UUID, now: datetime) -> dict[str, Any]:
    row = (
        conn.execute(
            select(
                posts.c.id,
                posts.c.group_id,
                posts.c.source_post_key,
                posts.c.permalink,
                groups.c.source,
            )
            .select_from(posts.join(groups, posts.c.group_id == groups.c.id))
            .where(posts.c.id == post_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["source"] != "fixture":
        raise WatchError("post_not_found", "Post was not found")
    if _active_grant(conn, cast(UUID, row["group_id"]), now) is None:
        raise WatchError("grant_unavailable", "An active capture grant is required")
    return dict(row)


def _public_watch(row: dict[str, Any]) -> dict[str, object]:
    return {
        "post_id": str(row["post_id"]),
        "saved_at": cast(datetime, row["saved_at"]).isoformat(),
        "hot_until": row["hot_until"].isoformat() if row["hot_until"] is not None else None,
        "next_due_at": (row["next_due_at"].isoformat() if row["next_due_at"] is not None else None),
    }


def _watch_row(conn: Connection, post_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
    query = select(watches).where(watches.c.post_id == post_id, watches.c.item_id.is_(None))
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().one_or_none()
    return dict(row) if row is not None else None


def add_watch(conn: Connection, now: datetime, post_id: UUID) -> dict[str, object]:
    """Add a post-level watch, returning its existing row when already saved."""
    normalized_now = _utc(now)
    with atomic(conn):
        _lock_watches(conn)
        _require_post(conn, post_id, normalized_now)
        row = _watch_row(conn, post_id, lock=True)
        if row is None:
            conn.execute(
                watches.insert().values(
                    id=uuid4(),
                    post_id=post_id,
                    item_id=None,
                    saved_at=normalized_now,
                    hot_until=None,
                    next_due_at=normalized_now + REVISIT_INTERVAL,
                )
            )
            row = cast(dict[str, Any], _watch_row(conn, post_id))
        return _public_watch(row)


def _post_first_seen(conn: Connection, post_id: UUID) -> datetime | None:
    return cast(
        datetime | None,
        conn.execute(
            select(func.min(observations.c.observed_at)).where(observations.c.post_id == post_id)
        ).scalar_one(),
    )


def _has_open_item(conn: Connection, post_id: UUID) -> bool:
    return (
        conn.execute(
            select(posts.c.id)
            .select_from(
                posts.outerjoin(listing_items, listing_items.c.post_id == posts.c.id)
                .outerjoin(current_items, current_items.c.item_id == listing_items.c.id)
                .outerjoin(sale_events, sale_events.c.id == current_items.c.status_event_id)
            )
            .where(
                posts.c.id == post_id,
                or_(
                    sale_events.c.sale_status.is_(None),
                    sale_events.c.sale_status.not_in(("sold", "withdrawn")),
                ),
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _another_watch_applies(conn: Connection, post_id: UUID, now: datetime) -> bool:
    first_seen = _post_first_seen(conn, post_id)
    new_window = first_seen is not None and first_seen + REVISIT_INTERVAL > now
    if new_window and _has_open_item(conn, post_id):
        return True
    rows = (
        conn.execute(
            select(watches.c.hot_until, watches.c.next_due_at)
            .where(watches.c.post_id == post_id)
            .order_by(watches.c.id)
        )
        .mappings()
        .all()
    )
    if any(row["hot_until"] is not None and row["hot_until"] > now for row in rows):
        return True
    if (
        first_seen is None
        or first_seen + REVISIT_INTERVAL > now
        or not _has_open_item(conn, post_id)
    ):
        return False
    return any(row["next_due_at"] is not None and row["next_due_at"] <= now for row in rows)


def remove_watch(conn: Connection, now: datetime, post_id: UUID) -> dict[str, object]:
    """Remove a post-level watch and cancel its queued revisit only if no reason remains."""
    normalized_now = _utc(now)
    with atomic(conn):
        _lock_watches(conn)
        _require_post(conn, post_id, normalized_now)
        row = _watch_row(conn, post_id, lock=True)
        if row is None:
            return {"post_id": str(post_id), "removed": False, "cancelled_revisit": False}
        conn.execute(delete(watches).where(watches.c.id == row["id"]))
        preserve_job = _another_watch_applies(conn, post_id, normalized_now)
        cancelled = False
        if not preserve_job:
            changed = conn.execute(
                update(jobs)
                .where(
                    jobs.c.kind == "revisit",
                    jobs.c.target_key == str(post_id),
                    jobs.c.state == "queued",
                )
                .values(state="cancelled")
            )
            cancelled = bool(changed.rowcount)
        return {
            "post_id": str(post_id),
            "removed": True,
            "cancelled_revisit": cancelled,
        }


def set_watch_hot(conn: Connection, now: datetime, post_id: UUID) -> dict[str, object]:
    """Open a 24-hour accelerated window, enforcing the global active-watch cap."""
    normalized_now = _utc(now)
    with atomic(conn):
        _lock_watches(conn)
        _require_post(conn, post_id, normalized_now)
        row = _watch_row(conn, post_id, lock=True)
        if row is None:
            raise WatchError("watch_not_found", "Watch was not found")
        currently_hot = row["hot_until"] is not None and row["hot_until"] > normalized_now
        if not currently_hot:
            active = conn.execute(
                select(func.count())
                .select_from(watches)
                .where(watches.c.hot_until > normalized_now)
            ).scalar_one()
            if active >= HOT_WATCH_LIMIT:
                raise WatchError("hot_watch_limit_reached", "At most 10 watches may be hot at once")
        hot_until = normalized_now + HOT_DURATION
        conn.execute(update(watches).where(watches.c.id == row["id"]).values(hot_until=hot_until))
        row["hot_until"] = hot_until
        return _public_watch(row)


def list_watches(conn: Connection, now: datetime) -> list[dict[str, object]]:
    """List watches whose fixture group currently has an active capture grant."""
    normalized_now = _utc(now)
    rows = (
        conn.execute(
            select(watches)
            .select_from(
                watches.join(posts, watches.c.post_id == posts.c.id).join(
                    groups, posts.c.group_id == groups.c.id
                )
            )
            .where(
                watches.c.item_id.is_(None),
                groups.c.source == "fixture",
                select(source_grants.c.id)
                .where(
                    source_grants.c.group_id == groups.c.id,
                    source_grants.c.source_kind == "fixture",
                    source_grants.c.revoked_at.is_(None),
                    source_grants.c.valid_from <= normalized_now,
                    or_(
                        source_grants.c.valid_until.is_(None),
                        source_grants.c.valid_until > normalized_now,
                    ),
                    source_grants.c.scopes.any("capture"),
                )
                .exists(),
            )
            .order_by(watches.c.saved_at, watches.c.post_id)
        )
        .mappings()
        .all()
    )
    return [_public_watch(dict(row)) for row in rows]
