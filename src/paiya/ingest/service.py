"""Fixture-only, transactional ingest; never performs requests to source websites."""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, insert, select, text, update

from paiya.extract.service import create_tags_and_rebuild_listing_items
from paiya.ingest.validation import validate_observation
from paiya.status.service import create_facts_and_rebuild_current_items
from paiya.store.fixture_scope import (
    FixtureScope,
    verify_fixture_scope,
    verify_fixture_scope_for_commit,
)
from paiya.store.media import validate_media, write_media
from paiya.store.tables import (
    capture_runs,
    comment_versions,
    comments,
    evidence,
    jobs,
    listing_items,
    media_assets,
    observations,
    posts,
    sellers,
)

PARSER_VERSION = "ingest-v1.0"


class CaptureConflict(ValueError):
    """The same capture ID was reused with different data."""


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _source_time(source_time: Mapping[str, Any] | None) -> datetime | None:
    if source_time is None or source_time.get("value") is None:
        return None
    return _at(cast(str, source_time["value"]))


def _get_or_create(
    conn: Connection, table: Any, criteria: dict[str, Any], values: dict[str, Any]
) -> UUID:
    found = conn.execute(select(table.c.id).filter_by(**criteria)).scalar_one_or_none()
    if found is not None:
        return cast(UUID, found)
    identifier = uuid4()
    conn.execute(insert(table).values(id=identifier, **values))
    return identifier


def _lock_capture_id(conn: Connection, capture_id: str) -> None:
    """Serialize replays of a capture before checking or creating its observation."""
    lock_key = int.from_bytes(
        hashlib.sha256(capture_id.encode("utf-8")).digest()[:8], byteorder="big", signed=True
    )
    conn.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})


def _comment_id_for_parent(
    conn: Connection,
    post_id: UUID,
    parent_key: str,
    current_comment_ids: Mapping[str, UUID],
) -> UUID | None:
    current = current_comment_ids.get(parent_key)
    if current is not None:
        return current
    return cast(
        UUID | None,
        conn.execute(
            select(comments.c.id).where(
                comments.c.post_id == post_id,
                comments.c.source_comment_key == parent_key,
            )
        ).scalar_one_or_none(),
    )


def ingest_fixture(
    engine: Engine,
    scope: FixtureScope,
    document: dict[str, Any],
    staged_assets: Mapping[str, bytes],
    media_root: Path,
) -> UUID:
    """Persist an authorized synthetic envelope, returning its observation UUID.

    Caller must pass a trusted FixtureScope created in fixture setup. File writes
    are atomic; an orphan from failed SQL is eligible for later cleanup.
    """
    validate_observation(document)
    if document["source_kind"] != "fixture" or document["grant_id"] is not None:
        raise PermissionError("A2 ingest accepts fixture sources with null source grant only")
    payload = document["payload"]
    if payload["group_key"] != scope.group_key:
        raise PermissionError("Ingest group does not match the trusted fixture grant")

    normalized = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    # Validate every staged byte before any database or disk writes.
    for meta in document["assets"].values():
        staging_key = meta["staging_key"]
        if staging_key not in staged_assets:
            raise ValueError(f"Missing staged asset: {staging_key}")
        validate_media(staging_key, meta, staged_assets[staging_key])

    observed_at = _at(payload["observed_at"])
    posted_at_data = payload.get("posted_at")
    posted_at = _source_time(posted_at_data)
    source_time_precision = posted_at_data["precision"] if posted_at_data is not None else "unknown"
    activity = payload.get("activity")

    with engine.begin() as conn:
        verify_fixture_scope(conn, scope, "capture")
        _lock_capture_id(conn, document["capture_id"])
        duplicate = (
            conn.execute(
                select(
                    observations.c.id,
                    observations.c.payload_hash,
                    observations.c.group_id,
                    observations.c.grant_id,
                ).where(observations.c.capture_id == document["capture_id"])
            )
            .mappings()
            .one_or_none()
        )
        if duplicate is not None:
            if duplicate["group_id"] != scope.group_id or duplicate["grant_id"] != scope.grant_id:
                raise PermissionError("capture_id belongs to another fixture scope")
            if duplicate["payload_hash"] != digest:
                raise CaptureConflict("capture_id reused with different envelope content")
            verify_fixture_scope_for_commit(conn, scope, "capture")
            return cast(UUID, duplicate["id"])

        raw_seller = payload.get("seller")
        seller_id = None
        if isinstance(raw_seller, dict) and raw_seller.get("external_actor_key"):
            stable_identity = {
                "source": "fixture",
                "identity_scope": raw_seller["identity_scope"],
                "external_actor_key": raw_seller["external_actor_key"],
            }
            seller_id = _get_or_create(conn, sellers, stable_identity, stable_identity)

        post_id = _get_or_create(
            conn,
            posts,
            {"group_id": scope.group_id, "source_post_key": payload["source_post_key"]},
            {
                "group_id": scope.group_id,
                "source_post_key": payload["source_post_key"],
                "permalink": payload["permalink"],
                "seller_id": seller_id,
                "posted_at": posted_at,
            },
        )
        post_updates: dict[str, Any] = {"permalink": payload["permalink"]}
        if seller_id is not None:
            post_updates["seller_id"] = seller_id
        if posted_at is not None:
            post_updates["posted_at"] = posted_at
        conn.execute(update(posts).where(posts.c.id == post_id).values(**post_updates))

        run_id = UUID(document["run_id"])
        existing_run = (
            conn.execute(
                select(
                    capture_runs.c.id,
                    capture_runs.c.group_id,
                    capture_runs.c.grant_id,
                ).where(capture_runs.c.id == run_id)
            )
            .mappings()
            .one_or_none()
        )
        if existing_run is None:
            job_id = uuid5(NAMESPACE_URL, f"paiya:fixture:job:{scope.group_id}:{run_id}")
            conn.execute(
                insert(jobs).values(
                    id=job_id,
                    group_id=scope.group_id,
                    grant_id=scope.grant_id,
                    kind=document["job_kind"],
                    logical_key=f"fixture:{scope.group_id}:{run_id}",
                    target_key=f"fixture:{scope.group_id}:{run_id}",
                    due_at=observed_at,
                    state="succeeded",
                    retry_count=0,
                )
            )
            conn.execute(
                insert(capture_runs).values(
                    id=run_id,
                    job_id=job_id,
                    group_id=scope.group_id,
                    grant_id=scope.grant_id,
                    source_kind="fixture",
                    started_at=observed_at,
                    session_result=document["page_check"]["session"],
                    sort_result=document["page_check"]["sort"],
                )
            )
        elif (
            existing_run["group_id"] != scope.group_id or existing_run["grant_id"] != scope.grant_id
        ):
            raise PermissionError("run_id belongs to another fixture grant")

        observation_id = uuid4()
        conn.execute(
            insert(observations).values(
                id=observation_id,
                run_id=run_id,
                post_id=post_id,
                group_id=scope.group_id,
                grant_id=scope.grant_id,
                capture_id=document["capture_id"],
                observed_at=observed_at,
                source_time=posted_at,
                source_time_precision=source_time_precision,
                access=payload["access"],
                coverage=activity["coverage"] if activity is not None else None,
                parser_version=PARSER_VERSION,
                source_reported_comment_count=(
                    activity["source_reported_comment_count"] if activity is not None else None
                ),
                distinct_comments_seen=(
                    activity["distinct_comments_seen"] if activity is not None else None
                ),
                distinct_known_commenters_seen=(
                    activity["distinct_known_commenters_seen"] if activity is not None else None
                ),
                payload_hash=digest,
                normalized_json=document,
            )
        )

        item_ids: dict[str, UUID] = {}
        evidence_to_items: dict[str, set[str]] = {}
        for entry in payload.get("items", []):
            source_item_key = entry["source_item_key"]
            if source_item_key is None:
                continue
            for evidence_key in entry.get("evidence_keys", []):
                evidence_to_items.setdefault(evidence_key, set()).add(source_item_key)
            price = entry.get("price")
            item_ids[source_item_key] = _get_or_create(
                conn,
                listing_items,
                {"post_id": post_id, "source_item_key": source_item_key},
                {
                    "post_id": post_id,
                    "source_item_key": source_item_key,
                    "model_key": None,
                    "variant": None,
                    "vram_gb": None,
                    "condition": None,
                    "warranty": None,
                    "warranty_until": None,
                    "unit": price["unit"] if price is not None else "unknown",
                    "resolution": "keyed",
                },
            )

        current_comment_ids: dict[str, UUID] = {}
        comment_rows: list[tuple[dict[str, Any], UUID]] = []
        for entry in payload.get("comments", []):
            source_comment_key = entry["source_comment_key"]
            if source_comment_key is None:
                comment_id = uuid4()
                conn.execute(
                    insert(comments).values(
                        id=comment_id,
                        post_id=post_id,
                        source_comment_key=None,
                        parent_id=None,
                    )
                )
            else:
                comment_id = _get_or_create(
                    conn,
                    comments,
                    {"post_id": post_id, "source_comment_key": source_comment_key},
                    {
                        "post_id": post_id,
                        "source_comment_key": source_comment_key,
                        "parent_id": None,
                    },
                )
                current_comment_ids[source_comment_key] = comment_id
            comment_rows.append((entry, comment_id))

        versions: dict[str, UUID] = {}
        for entry, comment_id in comment_rows:
            parent_key = entry.get("parent_key")
            if parent_key is not None:
                parent_id = _comment_id_for_parent(conn, post_id, parent_key, current_comment_ids)
                if parent_id is not None:
                    conn.execute(
                        update(comments)
                        .where(comments.c.id == comment_id)
                        .values(parent_id=parent_id)
                    )

            version_id = uuid4()
            source_comment_key = entry["source_comment_key"]
            conn.execute(
                insert(comment_versions).values(
                    id=version_id,
                    comment_id=comment_id,
                    observation_id=observation_id,
                    post_id=post_id,
                    role=entry["role"],
                    actor_key=entry["actor_key"],
                    body=entry["text"],
                    source_time=_source_time(entry["source_time"]),
                    body_hash=hashlib.sha256(entry["text"].encode("utf-8")).hexdigest(),
                )
            )
            if source_comment_key is not None:
                versions[source_comment_key] = version_id

        media_by_evidence: dict[str, UUID] = {}
        for local_key, meta in document["assets"].items():
            staging_key = meta["staging_key"]
            write_media(media_root, meta, staged_assets[staging_key], staging_key)
            existing_media = (
                conn.execute(
                    select(
                        media_assets.c.id,
                        media_assets.c.mime,
                        media_assets.c.bytes,
                        media_assets.c.width,
                        media_assets.c.height,
                    ).where(media_assets.c.sha256 == meta["sha256"])
                )
                .mappings()
                .one_or_none()
            )
            if existing_media:
                if any(
                    existing_media[field] != meta[field]
                    for field in ("mime", "bytes", "width", "height")
                ):
                    raise ValueError("Conflicting metadata for content-addressed media")
                media_id = cast(UUID, existing_media["id"])
            else:
                media_id = uuid4()
                conn.execute(
                    insert(media_assets).values(
                        id=media_id,
                        sha256=meta["sha256"],
                        storage_key=meta["sha256"],
                        mime=meta["mime"],
                        bytes=meta["bytes"],
                        width=meta["width"],
                        height=meta["height"],
                    )
                )
            media_by_evidence[local_key] = media_id

        for entry in payload.get("evidence", []):
            local_key = entry["local_key"]
            referenced_item_keys = evidence_to_items.get(local_key, set())
            item_id = (
                item_ids[next(iter(referenced_item_keys))]
                if len(referenced_item_keys) == 1
                else None
            )
            span = entry.get("source_span")
            linked_comment_version_id = (
                versions.get(span["comment_key"])
                if span is not None and span["field"] == "comment_text"
                else None
            )
            evidence_id = uuid4()
            conn.execute(
                insert(evidence).values(
                    id=evidence_id,
                    observation_id=observation_id,
                    post_id=post_id,
                    grant_id=scope.grant_id,
                    item_id=item_id,
                    comment_version_id=linked_comment_version_id,
                    media_asset_id=media_by_evidence.get(local_key),
                    local_key=local_key,
                    role=entry["role"],
                    source_span=span,
                    media_state=entry["media_state"],
                    media_reason=entry.get("media_reason"),
                    captured_at=observed_at,
                )
            )
        if payload["access"] == "ok":
            create_facts_and_rebuild_current_items(
                conn,
                observation_id=observation_id,
                post_id=post_id,
                observed_at=observed_at,
                payload=payload,
                item_ids=item_ids,
            )
            create_tags_and_rebuild_listing_items(
                conn,
                observation_id=observation_id,
                post_id=post_id,
                payload=payload,
                item_ids=item_ids,
            )

        # This is deliberately the final DB operation before transaction commit.
        verify_fixture_scope_for_commit(conn, scope, "capture")
        return observation_id
