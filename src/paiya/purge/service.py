"""Delete one authorized synthetic grant or post scope and rebuild projections."""

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from sqlalchemy import (
    Connection,
    Engine,
    and_,
    bindparam,
    delete,
    exists,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql import ColumnElement

from paiya.extract.service import recompute_listing_item_projection
from paiya.grants.collector import RETENTION, SOURCE_KIND, is_collector_scope
from paiya.grants.errors import GrantError
from paiya.grants.service import revoke_grant
from paiya.logging import emit_event
from paiya.purge.errors import PurgeError
from paiya.purge.log import PurgeLogEntry, ensure_unique_purge_ids, iter_purge_log
from paiya.purge.startup import count_pending_purge_ids
from paiya.status.service import rebuild_current_item_after_purge
from paiya.store.media import (
    lock_media_asset_for_session,
    lock_media_asset_for_transaction,
    media_path,
    unlock_media_asset_for_session,
)
from paiya.store.tables import (
    capture_gaps,
    capture_runs,
    comment_versions,
    comments,
    current_items,
    evidence,
    groups,
    jobs,
    listing_items,
    media_assets,
    observations,
    posts,
    price_facts,
    purge_jobs,
    sale_events,
    sellers,
    source_grants,
    status_facts,
    tags,
    watches,
)

_UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SCOPE = re.compile(rf"(?P<kind>grant|post|collector_run):(?P<key>{_UUID_TEXT})")
_PENDING_FILE_KEY = re.compile(r"(?P<storage_key>[0-9a-f]{64})\.(?P<extension>webp|jpg)")
_BACKUP_MEDIA_COPY_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"paiya:backup:media-copy").digest()[:8], byteorder="big", signed=True
)
_ITEM_REFERENCE_SQL = text(
    """
    SELECT o.post_id, o.id AS observation_id, o.observed_at,
           item_entry.value ->> 'source_item_key' AS source_item_key
    FROM observations AS o
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(o.normalized_json #> '{payload,items}') = 'array'
                THEN o.normalized_json #> '{payload,items}'
            ELSE '[]'::jsonb
        END
    ) AS item_entry(value)
    WHERE o.id IN :observation_ids
      AND item_entry.value ->> 'source_item_key' IS NOT NULL
    ORDER BY o.observed_at, o.id
    """
).bindparams(bindparam("observation_ids", expanding=True))


def _empty_counts() -> dict[str, object]:
    return {
        "current_items": 0,
        "current_items_rebuilt": 0,
        "sale_events": 0,
        "sale_events_rebuilt": 0,
        "watches": 0,
        "price_facts": 0,
        "status_facts": 0,
        "tags": 0,
        "evidence": 0,
        "comment_versions": 0,
        "observations": 0,
        "capture_runs": 0,
        "jobs": 0,
        "jobs_cancelled": 0,
        "capture_gaps": 0,
        "capture_gaps_updated": 0,
        "comments": 0,
        "listing_items": 0,
        "listing_items_recomputed": 0,
        "posts": 0,
        "sellers": 0,
        "media_assets": 0,
        "files_deleted": 0,
        "files_pending": 0,
        "pending_storage_keys": [],
    }


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_scope(scope: str) -> tuple[str, UUID, str]:
    match = _SCOPE.fullmatch(scope)
    if match is None:
        raise PurgeError(
            "scope_format_invalid",
            "Scope must be grant:<uuid>, post:<uuid> or collector_run:<uuid>",
        )
    kind = match.group("kind")
    key_text = match.group("key")
    try:
        scope_id = UUID(key_text)
    except ValueError as error:
        raise PurgeError(
            "scope_format_invalid",
            "Scope must be grant:<uuid>, post:<uuid> or collector_run:<uuid>",
        ) from error
    return kind, scope_id, f"{kind}:{scope_id}"


def validate_scope_format(scope: str) -> None:
    """Validate the CLI scope syntax before resolving database configuration."""
    _parse_scope(scope)


def _latest_success(conn: Connection, scope_kind: str, scope_key: str) -> Mapping[str, Any] | None:
    row = (
        conn.execute(
            select(purge_jobs.c.id, purge_jobs.c.result_counts)
            .where(
                purge_jobs.c.scope_kind == scope_kind,
                purge_jobs.c.scope_key == scope_key,
                purge_jobs.c.state == "succeeded",
            )
            .order_by(purge_jobs.c.requested_at.desc(), purge_jobs.c.id.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    return cast(Mapping[str, Any] | None, row)


def lock_backup_media_copy_exclusive_for_session(conn: Connection) -> None:
    conn.execute(
        text("SELECT pg_advisory_lock(:lock_key)"),
        {"lock_key": _BACKUP_MEDIA_COPY_LOCK_KEY},
    )


def unlock_backup_media_copy_exclusive_for_session(conn: Connection) -> None:
    conn.execute(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": _BACKUP_MEDIA_COPY_LOCK_KEY},
    )


def lock_backup_media_copy_shared_for_session(conn: Connection) -> None:
    conn.execute(
        text("SELECT pg_advisory_lock_shared(:lock_key)"),
        {"lock_key": _BACKUP_MEDIA_COPY_LOCK_KEY},
    )


def unlock_backup_media_copy_shared_for_session(conn: Connection) -> None:
    conn.execute(
        text("SELECT pg_advisory_unlock_shared(:lock_key)"),
        {"lock_key": _BACKUP_MEDIA_COPY_LOCK_KEY},
    )


def _collector_run_scope(conn: Connection, run_id: UUID, as_of: datetime) -> bool:
    row = (
        conn.execute(
            select(
                capture_runs.c.source_kind,
                capture_runs.c.started_at,
                groups.c.source,
                groups.c.group_key,
            )
            .join(groups, groups.c.id == capture_runs.c.group_id)
            .where(capture_runs.c.id == run_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False
    if (
        row["source_kind"] != SOURCE_KIND
        or not is_collector_scope(row["source"], row["group_key"])
        or row["started_at"] > as_of - RETENTION
    ):
        raise PurgeError("scope_not_permitted", "Only expired Collector runs may be purged")
    return True


def _validate_scope(conn: Connection, scope_kind: str, scope_id: UUID, scope_key: str) -> None:
    if scope_kind == "collector_run":
        if (
            not _collector_run_scope(conn, scope_id, datetime.now(UTC))
            and _latest_success(conn, scope_kind, scope_key) is None
        ):
            raise PurgeError("scope_not_found", "The Collector run was not found")
        return
    if scope_kind == "grant":
        row = (
            conn.execute(
                select(
                    source_grants.c.revoked_at,
                    groups.c.source,
                    groups.c.group_key,
                    source_grants.c.source_kind,
                )
                .select_from(source_grants.join(groups, groups.c.id == source_grants.c.group_id))
                .where(source_grants.c.id == scope_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise PurgeError("scope_not_found", "The grant was not found")
        if not (
            (row["source"] == "fixture" and cast(str, row["group_key"]).startswith("synthetic-"))
            or (
                row["source_kind"] == SOURCE_KIND
                and is_collector_scope(row["source"], row["group_key"])
            )
        ):
            raise PurgeError("scope_not_permitted", "The requested scope may not be purged")
        if row["revoked_at"] is None:
            raise PurgeError("grant_not_revoked", "The grant must be revoked before purge")
        return

    row = (
        conn.execute(
            select(posts.c.id, groups.c.source, groups.c.group_key)
            .select_from(posts.join(groups, groups.c.id == posts.c.group_id))
            .where(posts.c.id == scope_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        # A successful command is the durable synthetic-scope receipt for a deleted post.
        if _latest_success(conn, scope_kind, scope_key) is not None:
            return
        raise PurgeError("scope_not_found", "The post was not found")
    if row["source"] != "fixture" or not cast(str, row["group_key"]).startswith("synthetic-"):
        raise PurgeError("scope_not_permitted", "The requested scope may not be purged")


def _path_is_inside(root: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath((str(root), str(candidate)))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(root))


def _purge_paths() -> tuple[Path, Path]:
    raw_log_path = os.environ.get("PAIYA_PURGE_LOG_PATH")
    if not raw_log_path:
        raise PurgeError("purge_log_path_required", "PAIYA_PURGE_LOG_PATH must be set")
    raw_media_root = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
    if not raw_media_root:
        raise PurgeError("fixture_media_root_required", "PAIYA_FIXTURE_MEDIA_ROOT must be set")
    try:
        media_root = Path(raw_media_root).expanduser().resolve()
        log_path = Path(raw_log_path).expanduser().resolve()
    except OSError as error:
        raise PurgeError("purge_path_invalid", "Purge paths could not be resolved") from error
    if not media_root.is_dir():
        raise PurgeError("media_root_invalid", "PAIYA_FIXTURE_MEDIA_ROOT must be a directory")
    if _path_is_inside(media_root, log_path):
        raise PurgeError(
            "purge_log_path_not_permitted",
            "PAIYA_PURGE_LOG_PATH must be outside PAIYA_FIXTURE_MEDIA_ROOT",
        )
    if log_path.exists() and log_path.is_dir():
        raise PurgeError("purge_log_path_invalid", "PAIYA_PURGE_LOG_PATH must name a file")
    if not log_path.parent.is_dir():
        raise PurgeError("purge_log_path_invalid", "The purge log parent directory must exist")
    return media_root, log_path


def _append_log(path: Path, purge_id: UUID, scope_kind: str, scope_key: str, now: datetime) -> None:
    line = json.dumps(
        {
            "id": str(purge_id),
            "scope_kind": scope_kind,
            "scope_key": scope_key,
            "requested_at": _iso_utc(now),
        },
        separators=(",", ":"),
    )
    try:
        with path.open("ab") as stream:
            stream.write((line + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise PurgeError("purge_log_unavailable", "The purge log could not be appended") from error


def _scope_lock_id(scope_kind: str, scope_key: str) -> int:
    digest = hashlib.sha256(f"paiya:purge:{scope_kind}:{scope_key}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _lock_scope_for_session(conn: Connection, scope_kind: str, scope_key: str) -> None:
    conn.execute(
        text("SELECT pg_advisory_lock(:lock_key)"),
        {"lock_key": _scope_lock_id(scope_kind, scope_key)},
    )
    conn.commit()


def _unlock_scope_for_session(conn: Connection, scope_kind: str, scope_key: str) -> None:
    if conn.in_transaction():
        conn.rollback()
    conn.execute(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": _scope_lock_id(scope_kind, scope_key)},
    )
    conn.commit()


def _lock_scoped_posts(conn: Connection, scope_kind: str, scope_id: UUID) -> list[UUID]:
    if scope_kind in {"grant", "collector_run"}:
        target_post_ids = select(observations.c.post_id).where(
            _scope_observation_predicate(scope_kind, scope_id)
        )
        statement = select(posts.c.id).where(posts.c.id.in_(target_post_ids))
    else:
        statement = select(posts.c.id).where(posts.c.id == scope_id)
    return cast(
        list[UUID],
        conn.execute(statement.order_by(posts.c.id).with_for_update(of=posts)).scalars().all(),
    )


def _observation_item_rows(
    conn: Connection, observation_ids: Sequence[UUID]
) -> list[Mapping[str, Any]]:
    if not observation_ids:
        return []
    return cast(
        list[Mapping[str, Any]],
        conn.execute(_ITEM_REFERENCE_SQL, {"observation_ids": list(observation_ids)})
        .mappings()
        .all(),
    )


def _item_ids_for_observations(
    conn: Connection,
    item_column: ColumnElement[Any],
    observation_column: ColumnElement[Any],
    observation_ids: Sequence[UUID],
) -> set[UUID]:
    if not observation_ids:
        return set()
    rows: Sequence[Any] = (
        conn.execute(
            select(item_column).where(
                observation_column.in_(observation_ids),
                item_column.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    return {cast(UUID, item_id) for item_id in rows}


def _choose_latest(
    latest: dict[UUID, tuple[datetime, UUID]], item_id: UUID, observed_at: datetime, obs_id: UUID
) -> None:
    current = latest.get(item_id)
    if current is None or (observed_at, obs_id.int) > (current[0], current[1].int):
        latest[item_id] = (observed_at, obs_id)


def _comments_without_versions(conn: Connection, post_ids: Sequence[UUID]) -> int:
    if not post_ids:
        return 0
    total = 0
    child_comments = comments.alias("child_comments")
    while True:
        leaf_ids = select(comments.c.id).where(
            comments.c.post_id.in_(post_ids),
            ~exists(select(1).where(comment_versions.c.comment_id == comments.c.id)),
            ~exists(
                select(1).where(
                    child_comments.c.parent_id == comments.c.id,
                    child_comments.c.post_id == comments.c.post_id,
                )
            ),
        )
        result = conn.execute(
            delete(comments).where(comments.c.id.in_(leaf_ids)).returning(comments.c.id)
        )
        deleted = len(result.scalars().all())
        total += deleted
        if deleted == 0:
            return total


def _delete_count(conn: Connection, table: Any, predicate: ColumnElement[bool], key: Any) -> int:
    result = conn.execute(delete(table).where(predicate).returning(key))
    return len(result.scalars().all())


def _update_count(conn: Connection, statement: Any, key: Any) -> int:
    return len(conn.execute(statement.returning(key)).scalars().all())


def _scope_observation_predicate(scope_kind: str, scope_id: UUID) -> ColumnElement[bool]:
    if scope_kind == "grant":
        return observations.c.grant_id == scope_id
    if scope_kind == "collector_run":
        return observations.c.run_id == scope_id
    return observations.c.post_id == scope_id


def _purge_rows(
    conn: Connection, scope_kind: str, scope_id: UUID
) -> tuple[dict[str, object], list[tuple[str, str]]]:
    # The scoped posts are always locked before any domain-row mutation.
    locked_posts = _lock_scoped_posts(conn, scope_kind, scope_id)
    if scope_kind == "post" and not locked_posts:
        raise PurgeError("scope_not_found", "The post was not found")
    affected_post_ids = set(locked_posts)

    target_obs_rows = (
        conn.execute(
            select(observations.c.id, observations.c.post_id)
            .where(_scope_observation_predicate(scope_kind, scope_id))
            .order_by(observations.c.id)
        )
        .mappings()
        .all()
    )
    target_observation_ids = [cast(UUID, row["id"]) for row in target_obs_rows]
    target_post_ids = {cast(UUID, row["post_id"]) for row in target_obs_rows}
    affected_post_ids.update(target_post_ids)
    if scope_kind == "post":
        affected_post_ids.add(scope_id)

    if scope_kind != "post" and affected_post_ids:
        remaining_obs_rows = (
            conn.execute(
                select(observations.c.id, observations.c.post_id, observations.c.observed_at).where(
                    observations.c.post_id.in_(affected_post_ids),
                    ~_scope_observation_predicate(scope_kind, scope_id),
                )
            )
            .mappings()
            .all()
        )
    else:
        remaining_obs_rows = []

    remaining_observation_ids = [cast(UUID, row["id"]) for row in remaining_obs_rows]
    remaining_observation_times = {
        cast(UUID, row["id"]): cast(datetime, row["observed_at"]) for row in remaining_obs_rows
    }
    remaining_post_ids = {cast(UUID, row["post_id"]) for row in remaining_obs_rows}
    if scope_kind == "post":
        removed_post_ids = set(locked_posts)
    else:
        removed_post_ids = affected_post_ids - remaining_post_ids

    item_rows = (
        conn.execute(
            select(listing_items.c.id, listing_items.c.post_id, listing_items.c.source_item_key)
            .where(listing_items.c.post_id.in_(affected_post_ids))
            .order_by(listing_items.c.id)
        )
        .mappings()
        .all()
        if affected_post_ids
        else []
    )
    item_ids_by_key: dict[tuple[UUID, str], UUID] = {}
    all_item_ids: set[UUID] = set()
    item_posts: dict[UUID, UUID] = {}
    for item_row in item_rows:
        item_id = cast(UUID, item_row["id"])
        post_id = cast(UUID, item_row["post_id"])
        all_item_ids.add(item_id)
        item_posts[item_id] = post_id
        source_item_key = cast(str | None, item_row["source_item_key"])
        if source_item_key is not None:
            item_ids_by_key[(post_id, source_item_key)] = item_id

    target_item_ids: set[UUID] = set()
    latest_by_item: dict[UUID, tuple[datetime, UUID]] = {}
    for target_ref in _observation_item_rows(conn, target_observation_ids):
        target_post_id = cast(UUID, target_ref["post_id"])
        source_item_key = cast(str, target_ref["source_item_key"])
        target_item_id = item_ids_by_key.get((target_post_id, source_item_key))
        if target_item_id is not None:
            target_item_ids.add(target_item_id)

    target_item_ids.update(
        _item_ids_for_observations(
            conn, tags.c.item_id, tags.c.observation_id, target_observation_ids
        )
    )
    target_item_ids.update(
        _item_ids_for_observations(
            conn, price_facts.c.item_id, price_facts.c.observation_id, target_observation_ids
        )
    )
    target_item_ids.update(
        _item_ids_for_observations(
            conn, status_facts.c.item_id, status_facts.c.observation_id, target_observation_ids
        )
    )
    target_item_ids.update(
        _item_ids_for_observations(
            conn, evidence.c.item_id, evidence.c.observation_id, target_observation_ids
        )
    )
    if target_observation_ids:
        target_item_ids.update(
            cast(
                set[UUID],
                set(
                    conn.execute(
                        select(current_items.c.item_id).where(
                            current_items.c.last_observation_id.in_(target_observation_ids)
                        )
                    ).scalars()
                ),
            )
        )

    remaining_item_ids: set[UUID] = set()
    for remaining_ref in _observation_item_rows(conn, remaining_observation_ids):
        remaining_post_id = cast(UUID, remaining_ref["post_id"])
        source_item_key = cast(str, remaining_ref["source_item_key"])
        remaining_item_id = item_ids_by_key.get((remaining_post_id, source_item_key))
        if remaining_item_id is not None:
            remaining_item_ids.add(remaining_item_id)
            _choose_latest(
                latest_by_item,
                remaining_item_id,
                cast(datetime, remaining_ref["observed_at"]),
                cast(UUID, remaining_ref["observation_id"]),
            )
    for table, item_column, observation_column in (
        (tags, tags.c.item_id, tags.c.observation_id),
        (price_facts, price_facts.c.item_id, price_facts.c.observation_id),
        (status_facts, status_facts.c.item_id, status_facts.c.observation_id),
        (evidence, evidence.c.item_id, evidence.c.observation_id),
    ):
        if not remaining_observation_ids:
            continue
        rows = conn.execute(
            select(item_column, observation_column)
            .where(observation_column.in_(remaining_observation_ids), item_column.is_not(None))
            .distinct()
        ).all()
        for item_value, observation_value in rows:
            item_id = cast(UUID, item_value)
            observation_id = cast(UUID, observation_value)
            remaining_item_ids.add(item_id)
            observed_at = remaining_observation_times.get(observation_id)
            if observed_at is not None:
                _choose_latest(latest_by_item, item_id, observed_at, observation_id)
    if remaining_observation_ids:
        current_rows = conn.execute(
            select(current_items.c.item_id, current_items.c.last_observation_id).where(
                current_items.c.last_observation_id.in_(remaining_observation_ids)
            )
        ).all()
        for item_value, observation_value in current_rows:
            item_id = cast(UUID, item_value)
            observation_id = cast(UUID, observation_value)
            remaining_item_ids.add(item_id)
            observed_at = remaining_observation_times.get(observation_id)
            if observed_at is not None:
                _choose_latest(latest_by_item, item_id, observed_at, observation_id)

    orphan_item_ids = all_item_ids - remaining_item_ids
    rebuild_item_ids = (target_item_ids & remaining_item_ids) - orphan_item_ids
    if removed_post_ids:
        orphan_item_ids.update(
            item_id for item_id, post_id in item_posts.items() if post_id in removed_post_ids
        )
        rebuild_item_ids.difference_update(orphan_item_ids)
    projection_item_ids = orphan_item_ids | rebuild_item_ids

    counts = _empty_counts()
    if scope_kind == "post":
        counts["jobs_cancelled"] = _update_count(
            conn,
            update(jobs)
            .where(
                jobs.c.kind == "revisit",
                jobs.c.target_key == str(scope_id),
                jobs.c.state.in_(("queued", "leased")),
            )
            .values(state="cancelled", lease_token=None, lease_until=None),
            jobs.c.id,
        )

    if projection_item_ids:
        counts["current_items"] = _delete_count(
            conn,
            current_items,
            current_items.c.item_id.in_(projection_item_ids),
            current_items.c.item_id,
        )
        counts["sale_events"] = _delete_count(
            conn,
            sale_events,
            sale_events.c.item_id.in_(projection_item_ids),
            sale_events.c.id,
        )
    if orphan_item_ids or removed_post_ids:
        watch_filter = []
        if orphan_item_ids:
            watch_filter.append(watches.c.item_id.in_(orphan_item_ids))
        if removed_post_ids:
            watch_filter.append(watches.c.post_id.in_(removed_post_ids))
        predicate = or_(*watch_filter)
        counts["watches"] = _delete_count(conn, watches, predicate, watches.c.id)

    deleted_asset_rows: list[tuple[str, str]] = []
    asset_rows = (
        conn.execute(
            select(
                media_assets.c.id,
                media_assets.c.storage_key,
                media_assets.c.sha256,
                media_assets.c.mime,
            )
            .select_from(
                evidence.join(media_assets, evidence.c.media_asset_id == media_assets.c.id)
            )
            .where(evidence.c.observation_id.in_(target_observation_ids))
            .distinct()
        ).all()
        if target_observation_ids
        else []
    )
    for _asset_id, storage_key, _sha256, _mime in sorted(
        asset_rows, key=lambda row: cast(str, row[1])
    ):
        lock_media_asset_for_transaction(conn, cast(str, storage_key))

    if target_observation_ids:
        counts["price_facts"] = _delete_count(
            conn,
            price_facts,
            price_facts.c.observation_id.in_(target_observation_ids),
            price_facts.c.id,
        )
        counts["status_facts"] = _delete_count(
            conn,
            status_facts,
            status_facts.c.observation_id.in_(target_observation_ids),
            status_facts.c.id,
        )
        counts["tags"] = _delete_count(
            conn,
            tags,
            tags.c.observation_id.in_(target_observation_ids),
            tags.c.id,
        )
        counts["evidence"] = _delete_count(
            conn,
            evidence,
            evidence.c.observation_id.in_(target_observation_ids),
            evidence.c.id,
        )
        counts["comment_versions"] = _delete_count(
            conn,
            comment_versions,
            comment_versions.c.observation_id.in_(target_observation_ids),
            comment_versions.c.id,
        )
        counts["observations"] = _delete_count(
            conn,
            observations,
            observations.c.id.in_(target_observation_ids),
            observations.c.id,
        )

    if scope_kind == "collector_run":
        run_job: UUID = conn.execute(
            select(capture_runs.c.job_id).where(capture_runs.c.id == scope_id)
        ).scalar_one()
        counts["capture_runs"] = _delete_count(
            conn, capture_runs, capture_runs.c.id == scope_id, capture_runs.c.id
        )
        _delete_count(
            conn,
            capture_gaps,
            capture_gaps.c.id == uuid5(scope_id, "paiya:collector:gap"),
            capture_gaps.c.id,
        )
        # Keep an opaque duplicate marker in the purge log; discard the job and its result.
        counts["jobs"] = _delete_count(conn, jobs, jobs.c.id == run_job, jobs.c.id)

    if scope_kind == "grant":
        collector_gaps = select(capture_runs.c.id).where(
            capture_runs.c.grant_id == scope_id, capture_runs.c.source_kind == SOURCE_KIND
        )
        collector_run_ids: list[UUID] = list(conn.execute(collector_gaps).scalars())
        for run_id in collector_run_ids:
            _delete_count(
                conn,
                capture_gaps,
                capture_gaps.c.id == uuid5(run_id, "paiya:collector:gap"),
                capture_gaps.c.id,
            )
        grant_job_ids: list[UUID] = list(
            conn.execute(select(jobs.c.id).where(jobs.c.grant_id == scope_id)).scalars().all()
        )
        if grant_job_ids:
            counts["capture_gaps_updated"] = _update_count(
                conn,
                update(capture_gaps)
                .where(capture_gaps.c.compensated_by_job_id.in_(grant_job_ids))
                .values(compensated_by_job_id=None),
                capture_gaps.c.id,
            )
        counts["capture_runs"] = _delete_count(
            conn,
            capture_runs,
            capture_runs.c.grant_id == scope_id,
            capture_runs.c.id,
        )
        counts["jobs"] = _delete_count(conn, jobs, jobs.c.grant_id == scope_id, jobs.c.id)

    if removed_post_ids:
        # Whole-post deletion may contain comment parent cycles; clear only in-scope links.
        conn.execute(
            update(comments).where(comments.c.post_id.in_(removed_post_ids)).values(parent_id=None)
        )
        counts["comments"] = _delete_count(
            conn,
            comments,
            comments.c.post_id.in_(removed_post_ids),
            comments.c.id,
        )
    else:
        counts["comments"] = _comments_without_versions(conn, list(affected_post_ids))

    if target_observation_ids:
        asset_ids = [cast(UUID, row[0]) for row in asset_rows]
        orphan_assets = conn.execute(
            delete(media_assets)
            .where(
                media_assets.c.id.in_(asset_ids),
                ~exists(select(1).where(evidence.c.media_asset_id == media_assets.c.id)),
            )
            .returning(media_assets.c.storage_key, media_assets.c.sha256, media_assets.c.mime)
        ).all()
        counts["media_assets"] = len(orphan_assets)
        for storage_key, sha256, mime in orphan_assets:
            file_key = media_path(Path("."), cast(str, sha256), cast(str, mime)).name
            deleted_asset_rows.append((cast(str, storage_key), file_key))

    if orphan_item_ids:
        counts["listing_items"] = _delete_count(
            conn,
            listing_items,
            listing_items.c.id.in_(orphan_item_ids),
            listing_items.c.id,
        )

    counts["current_items_rebuilt"] = 0
    for item_id in sorted(rebuild_item_ids, key=str):
        latest = latest_by_item.get(item_id)
        item_post_id = item_posts.get(item_id)
        if latest is None or item_post_id is None:
            continue
        event_count = rebuild_current_item_after_purge(
            conn,
            item_id=item_id,
            post_id=item_post_id,
            observation_id=latest[1],
            observed_at=latest[0],
        )
        counts["current_items_rebuilt"] = cast(int, counts["current_items_rebuilt"]) + 1
        counts["sale_events_rebuilt"] = cast(int, counts["sale_events_rebuilt"]) + event_count
        if recompute_listing_item_projection(conn, item_id):
            counts["listing_items_recomputed"] = cast(int, counts["listing_items_recomputed"]) + 1

    if removed_post_ids:
        seller_ids: list[UUID] = list(
            conn.execute(
                select(posts.c.seller_id)
                .where(posts.c.id.in_(removed_post_ids), posts.c.seller_id.is_not(None))
                .distinct()
            ).scalars()
        )
        counts["posts"] = _delete_count(conn, posts, posts.c.id.in_(removed_post_ids), posts.c.id)
        if seller_ids:
            counts["sellers"] = _delete_count(
                conn,
                sellers,
                and_(
                    sellers.c.id.in_(seller_ids),
                    ~exists(select(1).where(posts.c.seller_id == sellers.c.id)),
                ),
                sellers.c.id,
            )

    # capture_gaps are group-level records and are never deleted by a scope purge.
    return counts, [(storage_key, file_key) for storage_key, file_key in deleted_asset_rows]


def _record_failed(
    conn: Connection,
    purge_id: UUID,
    reason: str,
) -> None:
    if conn.in_transaction():
        conn.rollback()
    with conn.begin():
        conn.execute(
            update(purge_jobs)
            .where(purge_jobs.c.id == purge_id)
            .values(
                state="failed",
                completed_at=datetime.now(UTC),
                reason=reason,
                result_counts=_empty_counts(),
            )
        )


def _replay_scope_status(conn: Connection, scope_kind: str, scope_id: UUID) -> tuple[bool, bool]:
    """Return whether a replay scope exists and whether its grant needs revocation."""
    if scope_kind == "collector_run":
        return _collector_run_scope(conn, scope_id, datetime.now(UTC)), False
    if scope_kind == "grant":
        row = (
            conn.execute(
                select(
                    source_grants.c.revoked_at,
                    groups.c.source,
                    groups.c.group_key,
                    source_grants.c.source_kind,
                )
                .select_from(source_grants.join(groups, groups.c.id == source_grants.c.group_id))
                .where(source_grants.c.id == scope_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return False, False
        if not (
            (row["source"] == "fixture" and cast(str, row["group_key"]).startswith("synthetic-"))
            or (
                row["source_kind"] == SOURCE_KIND
                and is_collector_scope(row["source"], row["group_key"])
            )
        ):
            raise PurgeError("scope_not_permitted", "The requested scope may not be purged")
        return True, row["revoked_at"] is None

    row = (
        conn.execute(
            select(groups.c.source, groups.c.group_key)
            .select_from(posts.join(groups, groups.c.id == posts.c.group_id))
            .where(posts.c.id == scope_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False, False
    if row["source"] != "fixture" or not cast(str, row["group_key"]).startswith("synthetic-"):
        raise PurgeError("scope_not_permitted", "The requested scope may not be purged")
    return True, False


def _same_purge_identity(row: RowMapping, entry: PurgeLogEntry, scope_text: str) -> bool:
    return (
        row["scope_kind"] == entry.scope_kind
        and row["scope_key"] == entry.scope_key
        and row["requested_at"] == entry.requested_at
        and scope_text == entry.scope
    )


def _job_result(
    purge_id: UUID, scope_text: str, state: str, result_counts: object
) -> dict[str, object]:
    return {
        "purge_id": str(purge_id),
        "scope": scope_text,
        "state": state,
        "result_counts": result_counts,
    }


def _start_purge_job(
    conn: Connection,
    purge_id: UUID,
    scope_kind: str,
    scope_key: str,
    requested_at: datetime,
    *,
    replay_entry: PurgeLogEntry | None,
) -> dict[str, object] | None:
    """Insert or resume the command row; return its prior result if already complete."""
    values = {
        "id": purge_id,
        "scope_kind": scope_kind,
        "scope_key": scope_key,
        "requested_at": requested_at,
        "state": "running",
        "completed_at": None,
        "reason": None,
        "result_counts": _empty_counts(),
    }
    with conn.begin():
        if replay_entry is None:
            conn.execute(insert(purge_jobs).values(**values))
            return None

        inserted_id = conn.execute(
            pg_insert(purge_jobs)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[purge_jobs.c.id])
            .returning(purge_jobs.c.id)
        ).scalar_one_or_none()
        if inserted_id is not None:
            return None

        existing = (
            conn.execute(
                select(
                    purge_jobs.c.scope_kind,
                    purge_jobs.c.scope_key,
                    purge_jobs.c.requested_at,
                    purge_jobs.c.state,
                    purge_jobs.c.result_counts,
                )
                .where(purge_jobs.c.id == purge_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if existing is None or not _same_purge_identity(existing, replay_entry, replay_entry.scope):
            raise PurgeError("purge_log_invalid", "A purge ID conflicts with its recorded command")
        if existing["state"] == "succeeded":
            return _job_result(
                purge_id,
                replay_entry.scope,
                "succeeded",
                existing["result_counts"],
            )
        conn.execute(
            update(purge_jobs)
            .where(purge_jobs.c.id == purge_id)
            .values(state="running", completed_at=None, reason=None)
        )
    return None


def _execute_purge_command(
    engine: Engine,
    scope: str,
    *,
    purge_id: UUID | None = None,
    requested_at: datetime | None = None,
    append_log: bool,
    replay_entry: PurgeLogEntry | None = None,
    media_root: Path | None = None,
    log_path: Path | None = None,
) -> dict[str, object]:
    """Run the shared purge path using a new logged ID or an existing replay ID."""
    scope_kind, scope_id, scope_text = _parse_scope(scope)
    scope_key = str(scope_id)
    scope_exists = True

    with engine.connect() as conn:
        _lock_scope_for_session(conn, scope_kind, scope_key)
        try:
            if append_log:
                with conn.begin():
                    _validate_scope(conn, scope_kind, scope_id, scope_key)
                    previous = _latest_success(conn, scope_kind, scope_key)
            else:
                if replay_entry is None or purge_id is None or requested_at is None:
                    raise ValueError("Replay requires an existing purge command identity")
                if (purge_id, requested_at, scope_text) != (
                    replay_entry.id,
                    replay_entry.requested_at,
                    replay_entry.scope,
                ):
                    raise ValueError("Replay command identity does not match its log entry")
                with conn.begin():
                    scope_exists, needs_revoke = _replay_scope_status(conn, scope_kind, scope_id)
                    existing = (
                        conn.execute(
                            select(
                                purge_jobs.c.id,
                                purge_jobs.c.scope_kind,
                                purge_jobs.c.scope_key,
                                purge_jobs.c.requested_at,
                                purge_jobs.c.state,
                                purge_jobs.c.result_counts,
                            )
                            .where(purge_jobs.c.id == purge_id)
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing is not None:
                        if not _same_purge_identity(existing, replay_entry, scope_text):
                            raise PurgeError(
                                "purge_log_invalid",
                                "A purge ID conflicts with its recorded command",
                            )
                        if existing["state"] == "succeeded":
                            return _job_result(
                                purge_id,
                                scope_text,
                                "succeeded",
                                existing["result_counts"],
                            )
                    previous = _latest_success(conn, scope_kind, scope_key)
                    if (
                        not scope_exists
                        and existing is not None
                        and existing["result_counts"]["pending_storage_keys"]
                    ):
                        previous = dict(existing)

                if needs_revoke:
                    try:
                        revoke_grant(engine, scope_id)
                    except GrantError as error:
                        raise PurgeError(error.code, error.message) from error
                if scope_exists:
                    with conn.begin():
                        _validate_scope(conn, scope_kind, scope_id, scope_key)

            if media_root is None or log_path is None:
                media_root, log_path = _purge_paths()
            assert media_root is not None and log_path is not None

            if append_log:
                purge_id = uuid4()
                requested_at = datetime.now(UTC)
                _append_log(log_path, purge_id, scope_kind, scope_key, requested_at)
            elif purge_id is None or requested_at is None:
                raise ValueError("Replay requires an existing purge command identity")
            assert purge_id is not None and requested_at is not None

            if not append_log and not scope_exists and previous is None:
                counts = _empty_counts()
                completed = datetime.now(UTC)
                prior_result = _start_purge_job(
                    conn,
                    purge_id,
                    scope_kind,
                    scope_key,
                    requested_at,
                    replay_entry=replay_entry,
                )
                if prior_result is not None:
                    return prior_result
                with conn.begin():
                    conn.execute(
                        update(purge_jobs)
                        .where(purge_jobs.c.id == purge_id)
                        .values(
                            state="succeeded",
                            completed_at=completed,
                            reason=None,
                            result_counts=counts,
                        )
                    )
                return _job_result(purge_id, scope_text, "succeeded", counts)

            prior_result = _start_purge_job(
                conn,
                purge_id,
                scope_kind,
                scope_key,
                requested_at,
                replay_entry=replay_entry,
            )
            if prior_result is not None:
                return prior_result

            counts = _empty_counts()
            deleted_asset_keys: list[str] = []
            if previous is None:
                try:
                    with conn.begin():
                        _validate_scope(conn, scope_kind, scope_id, scope_key)
                        counts, deleted_assets = _purge_rows(conn, scope_kind, scope_id)
                        deleted_asset_keys = [file_key for _storage_key, file_key in deleted_assets]
                        counts["pending_storage_keys"] = deleted_asset_keys
                        counts["files_pending"] = len(deleted_asset_keys)
                        conn.execute(
                            update(purge_jobs)
                            .where(purge_jobs.c.id == purge_id)
                            .values(result_counts=counts)
                        )
                except PurgeError as error:
                    _record_failed(conn, purge_id, error.code)
                    return {
                        "purge_id": str(purge_id),
                        "scope": scope_text,
                        "state": "failed",
                        "reason": error.code,
                        "result_counts": _empty_counts(),
                    }
                except Exception:
                    _record_failed(conn, purge_id, "transaction_failed")
                    return {
                        "purge_id": str(purge_id),
                        "scope": scope_text,
                        "state": "failed",
                        "reason": "transaction_failed",
                        "result_counts": _empty_counts(),
                    }
                files_deleted, pending_keys = _retry_file_deletes(
                    conn, media_root, deleted_asset_keys
                )
            else:
                previous_counts = cast(Mapping[str, Any], previous["result_counts"])
                if not append_log and previous["id"] == purge_id:
                    counts.update(previous_counts)
                previous_pending = previous_counts.get("pending_storage_keys", [])
                pending_candidates = (
                    cast(list[str], previous_pending) if isinstance(previous_pending, list) else []
                )
                files_deleted, pending_keys = _retry_file_deletes(
                    conn, media_root, pending_candidates
                )

            counts["files_deleted"] = files_deleted
            counts["files_pending"] = len(pending_keys)
            counts["pending_storage_keys"] = pending_keys
            final_state = (
                "failed" if scope_kind == "collector_run" and pending_keys else "succeeded"
            )
            with conn.begin():
                conn.execute(
                    update(purge_jobs)
                    .where(purge_jobs.c.id == purge_id)
                    .values(
                        state=final_state,
                        completed_at=datetime.now(UTC),
                        reason="files_pending" if final_state == "failed" else None,
                        result_counts=counts,
                    )
                )
            return _job_result(purge_id, scope_text, final_state, counts)
        finally:
            _unlock_scope_for_session(conn, scope_kind, scope_key)


def _retry_file_deletes(
    conn: Connection, media_root: Path, pending_keys: Sequence[str]
) -> tuple[int, list[str]]:
    if conn.in_transaction():
        conn.commit()
    lock_backup_media_copy_shared_for_session(conn)
    conn.commit()
    try:
        return _retry_file_deletes_with_asset_locks(conn, media_root, pending_keys)
    finally:
        if conn.in_transaction():
            conn.rollback()
        unlock_backup_media_copy_shared_for_session(conn)
        conn.commit()


def _retry_file_deletes_with_asset_locks(
    conn: Connection, media_root: Path, pending_keys: Sequence[str]
) -> tuple[int, list[str]]:
    files_deleted = 0
    still_pending: list[str] = []
    for file_key in dict.fromkeys(pending_keys):
        match = _PENDING_FILE_KEY.fullmatch(file_key)
        if match is None:
            still_pending.append(file_key)
            continue
        storage_key = match.group("storage_key")
        lock_media_asset_for_session(conn, storage_key)
        conn.commit()
        try:
            with conn.begin():
                current_asset = (
                    conn.execute(
                        select(
                            media_assets.c.id,
                            exists(
                                select(1).where(evidence.c.media_asset_id == media_assets.c.id)
                            ).label("has_evidence"),
                        ).where(media_assets.c.storage_key == storage_key)
                    )
                    .mappings()
                    .one_or_none()
                )
            if current_asset is not None:
                if not current_asset["has_evidence"]:
                    still_pending.append(file_key)
                continue
            file_path = media_root / "assets" / file_key
            try:
                resolved_file_path = file_path.resolve()
            except (OSError, RuntimeError):
                still_pending.append(file_key)
                continue
            if not _path_is_inside(media_root, resolved_file_path):
                still_pending.append(file_key)
                continue
            try:
                file_path.unlink()
                files_deleted += 1
            except FileNotFoundError:
                pass
            except OSError:
                still_pending.append(file_key)
        finally:
            if conn.in_transaction():
                conn.rollback()
            unlock_media_asset_for_session(conn, storage_key)
            conn.commit()
    return files_deleted, still_pending


def run_purge(engine: Engine, scope: str) -> dict[str, object]:
    """Purge one synthetic grant or post, logging before durable database changes."""
    result = _execute_purge_command(engine, scope, append_log=True)
    _log_purge_finished(result)
    return result


def _log_purge_finished(result: Mapping[str, object]) -> None:
    result_counts = result.get("result_counts")
    counts = result_counts if isinstance(result_counts, Mapping) else {}
    pending_count = counts.get("files_pending", 0)
    if not isinstance(pending_count, int) or isinstance(pending_count, bool):
        pending_count = 0
    emit_event(
        "purge_finished",
        purge_id=str(result["purge_id"]),
        result=str(result["state"]),
        pending_count=max(0, pending_count),
    )


def replay_purge_log(engine: Engine) -> dict[str, object]:
    """Replay pending synthetic purge commands in append order without appending."""
    media_root, log_path = _purge_paths()
    results: list[dict[str, object]] = []
    seen: dict[UUID, PurgeLogEntry] = {}

    for entry in iter_purge_log(log_path, missing_ok=True):
        previous_entry = seen.get(entry.id)
        if previous_entry is not None:
            if previous_entry != entry:
                raise PurgeError("purge_log_invalid", "The purge log reuses an ID inconsistently")
            continue
        seen[entry.id] = entry
        result = _execute_purge_command(
            engine,
            entry.scope,
            purge_id=entry.id,
            requested_at=entry.requested_at,
            append_log=False,
            replay_entry=entry,
            media_root=media_root,
            log_path=log_path,
        )
        _log_purge_finished(result)
        results.append(
            {key: result[key] for key in ("purge_id", "scope", "state", "result_counts")}
        )

    final_entries = ensure_unique_purge_ids(iter_purge_log(log_path, missing_ok=True))
    pending_count = count_pending_purge_ids(engine, final_entries)
    return {"results": results, "pending_count": pending_count}
