"""Fixture-only, transactional ingest; never performs requests to source websites."""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, insert, select, update

from paiya.ingest.validation import validate_observation
from paiya.store.fixture_scope import FixtureScope, verify_fixture_scope
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


class CaptureConflict(ValueError):
    """The same capture ID was reused with different data."""


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get_or_create(
    conn: Connection, table: Any, criteria: dict[str, Any], values: dict[str, Any]
) -> UUID:
    found = conn.execute(select(table.c.id).filter_by(**criteria)).scalar_one_or_none()
    if found is not None:
        return found
    identifier = uuid4()
    conn.execute(insert(table).values(id=identifier, **values))
    return identifier


def ingest_fixture(
    engine: Engine,
    scope: FixtureScope,
    document: dict[str, Any],
    staged_assets: Mapping[str, bytes],
    media_root: Path,
) -> UUID:
    """Persist an authorized synthetic envelope, returning its observation UUID.

    Caller must pass a trusted FixtureScope created in the fixture test setup.
    File writes are atomic; an orphan from failed SQL is eligible for later cleanup.
    """
    validate_observation(document)
    if document["source_kind"] != "fixture" or document["grant_id"] is not None:
        raise PermissionError("A2 ingest accepts fixture sources with null source grant only")
    payload = document["payload"]
    if payload["group_key"] != scope.group_key:
        raise PermissionError("Ingest group does not match the trusted fixture grant")

    normalized = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    # Verify staged bytes *before* making any DB or disk writes.
    for meta in document["assets"].values():
        key = meta["staging_key"]
        if key not in staged_assets:
            raise ValueError(f"Missing staged asset: {key}")
        validate_media(key, meta, staged_assets[key])

    with engine.begin() as conn:
        verify_fixture_scope(conn, scope, "capture")
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
            return cast(UUID, duplicate["id"])

        raw_seller = payload.get("seller")
        seller_id = None
        if isinstance(raw_seller, dict) and raw_seller.get("external_actor_key"):
            stable_id = {
                "source_kind": "fixture",
                "identity_scope": raw_seller["identity_scope"],
                "external_actor_key": raw_seller["external_actor_key"],
            }
            seller_id = _get_or_create(
                conn,
                sellers,
                stable_id,
                {**stable_id, "display_name": raw_seller.get("display_name")},
            )

        post_id = _get_or_create(
            conn,
            posts,
            {"group_id": scope.group_id, "source_post_key": payload["source_post_key"]},
            {
                "group_id": scope.group_id,
                "source_post_key": payload["source_post_key"],
                "permalink": payload["permalink"],
                "seller_id": seller_id,
            },
        )
        if seller_id is not None:
            conn.execute(update(posts).where(posts.c.id == post_id).values(seller_id=seller_id))

        run_id = UUID(document["run_id"])
        existing_run = (
            conn.execute(
                select(capture_runs.c.id, capture_runs.c.group_id, capture_runs.c.grant_id).where(
                    capture_runs.c.id == run_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing_run is None:
            job_id = uuid5(NAMESPACE_URL, f"paiya:fixture:job:{run_id}")
            conn.execute(
                insert(jobs).values(
                    id=job_id,
                    group_id=scope.group_id,
                    grant_id=scope.grant_id,
                    kind=document["job_kind"],
                    logical_key=f"fixture:{run_id}",
                )
            )
            conn.execute(
                insert(capture_runs).values(
                    id=run_id,
                    job_id=job_id,
                    group_id=scope.group_id,
                    grant_id=scope.grant_id,
                    source_kind="fixture",
                    job_kind=document["job_kind"],
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
                observed_at=_at(payload["observed_at"]),
                access=payload["access"],
                activity=payload.get("activity"),
                payload_hash=digest,
                normalized_json=document,
            )
        )
        item_ids: dict[str, UUID] = {}
        evidence_to_items: dict[str, set[str]] = {}
        for entry in payload.get("items", []):
            key = entry["source_item_key"]
            for evidence_key in entry.get("evidence_keys", []):
                if key is not None:
                    evidence_to_items.setdefault(evidence_key, set()).add(key)
            if key is not None:
                item_ids[key] = _get_or_create(
                    conn,
                    listing_items,
                    {"post_id": post_id, "source_item_key": key},
                    {
                        "post_id": post_id,
                        "source_item_key": key,
                        "model": entry.get("model"),
                        "condition_text": entry.get("condition_text"),
                    },
                )

        versions: dict[str, UUID] = {}
        for entry in payload.get("comments", []):
            key = entry["source_comment_key"]
            if key is None:
                continue
            comment_id = _get_or_create(
                conn,
                comments,
                {"post_id": post_id, "source_comment_key": key},
                {"post_id": post_id, "source_comment_key": key},
            )
            version_id = uuid4()
            conn.execute(
                insert(comment_versions).values(
                    id=version_id,
                    comment_id=comment_id,
                    observation_id=observation_id,
                    role=entry["role"],
                    actor_key=entry.get("actor_key"),
                    body=entry["text"],
                    source_time=entry["source_time"],
                )
            )
            versions[key] = version_id

        media_by_evidence: dict[str, UUID] = {}
        for local_key, meta in document["assets"].items():
            key = meta["staging_key"]
            write_media(media_root, meta, staged_assets[key], key)
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
            referenced = evidence_to_items.get(local_key, set())
            item_id = item_ids[next(iter(referenced))] if len(referenced) == 1 else None
            span = entry.get("source_span")
            linked_comment_version_id = (
                versions.get(span["comment_key"])
                if span is not None and span["field"] == "comment_text"
                else None
            )
            conn.execute(
                insert(evidence).values(
                    id=uuid4(),
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
                )
            )
        return observation_id
