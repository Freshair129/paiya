"""Scope-checked read operations, independent of FastAPI and transport details."""

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Connection, select

from paiya.read.context import ReadContext
from paiya.read.errors import (
    GrantUnavailable,
    MediaIntegrityFailure,
    MediaNotAvailable,
    MediaStorageUnavailable,
    NotFound,
)
from paiya.store.fixture_scope import verify_fixture_scope
from paiya.store.media import media_path
from paiya.store.tables import evidence, listing_items, media_assets, observations, posts


@dataclass(frozen=True)
class MediaContent:
    """Verified bytes ready for a transport adapter to return."""

    data: bytes
    mime: str


def _authorize(conn: Connection, context: ReadContext, purpose: str) -> None:
    try:
        verify_fixture_scope(conn, context, purpose)
    except PermissionError as error:
        raise GrantUnavailable() from error


def _evidence_row(conn: Connection, context: ReadContext, evidence_id: UUID) -> Mapping[str, Any]:
    row = (
        conn.execute(
            select(
                evidence.c.id,
                evidence.c.local_key,
                evidence.c.observation_id,
                evidence.c.post_id,
                evidence.c.item_id,
                evidence.c.comment_version_id,
                evidence.c.media_asset_id,
                evidence.c.role,
                evidence.c.source_span,
                evidence.c.media_state,
                evidence.c.media_reason,
                evidence.c.captured_at,
                posts.c.permalink,
                media_assets.c.sha256,
                media_assets.c.mime,
                media_assets.c.bytes,
                media_assets.c.width,
                media_assets.c.height,
            )
            .select_from(
                evidence.join(observations, evidence.c.observation_id == observations.c.id)
                .join(posts, evidence.c.post_id == posts.c.id)
                .outerjoin(media_assets, evidence.c.media_asset_id == media_assets.c.id)
            )
            .where(
                evidence.c.id == evidence_id,
                evidence.c.grant_id == context.grant_id,
                observations.c.group_id == context.group_id,
                observations.c.grant_id == context.grant_id,
                posts.c.group_id == context.group_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFound()
    return cast(Mapping[str, Any], row)


def read_post(conn: Connection, context: ReadContext, post_id: UUID) -> dict[str, Any]:
    """Return the post snapshot and its observations inside the trusted grant."""
    _authorize(conn, context, "read")
    post = (
        conn.execute(
            select(
                posts.c.id,
                posts.c.group_id,
                posts.c.source_post_key,
                posts.c.permalink,
                posts.c.seller_id,
                posts.c.posted_at,
            ).where(posts.c.id == post_id, posts.c.group_id == context.group_id)
        )
        .mappings()
        .one_or_none()
    )
    if post is None:
        raise NotFound()

    observed = (
        conn.execute(
            select(
                observations.c.id,
                observations.c.run_id,
                observations.c.capture_id,
                observations.c.observed_at,
                observations.c.access,
                observations.c.coverage,
                observations.c.source_reported_comment_count,
                observations.c.distinct_comments_seen,
                observations.c.distinct_known_commenters_seen,
                observations.c.normalized_json,
            )
            .where(
                observations.c.post_id == post_id,
                observations.c.group_id == context.group_id,
                observations.c.grant_id == context.grant_id,
            )
            .order_by(observations.c.observed_at, observations.c.id)
        )
        .mappings()
        .all()
    )
    if not observed:
        raise NotFound()

    observation_ids = {cast(UUID, row["id"]) for row in observed}
    evidence_rows = (
        conn.execute(
            select(evidence.c.id, evidence.c.observation_id, evidence.c.local_key)
            .where(
                evidence.c.post_id == post_id,
                evidence.c.grant_id == context.grant_id,
                evidence.c.observation_id.in_(observation_ids),
            )
            .order_by(evidence.c.id)
        )
        .mappings()
        .all()
    )
    evidence_by_observation: dict[UUID, dict[str, UUID]] = {}
    evidence_ids_by_observation: dict[UUID, list[UUID]] = {}
    for row in evidence_rows:
        observation_id = cast(UUID, row["observation_id"])
        evidence_id = cast(UUID, row["id"])
        evidence_by_observation.setdefault(observation_id, {})[row["local_key"]] = evidence_id
        evidence_ids_by_observation.setdefault(observation_id, []).append(evidence_id)

    item_ids = {
        cast(str, row["source_item_key"]): cast(UUID, row["id"])
        for row in conn.execute(
            select(listing_items.c.id, listing_items.c.source_item_key).where(
                listing_items.c.post_id == post_id,
                listing_items.c.source_item_key.is_not(None),
            )
        )
        .mappings()
        .all()
    }

    result_observations: list[dict[str, Any]] = []
    for row in observed:
        observation_id = cast(UUID, row["id"])
        payload = cast(Mapping[str, Any], row["normalized_json"]["payload"])
        local_evidence = evidence_by_observation.get(observation_id, {})
        raw_items = cast(list[Mapping[str, Any]], payload.get("items", []))
        result_items: list[dict[str, Any]] = []
        for item in raw_items:
            source_item_key = cast(str | None, item.get("source_item_key"))
            result_items.append(
                {
                    "item_id": item_ids.get(source_item_key)
                    if source_item_key is not None
                    else None,
                    "source_item_key": source_item_key,
                    "model": item.get("model"),
                    "condition_text": item.get("condition_text"),
                    "source_status_label": item.get("source_status_label"),
                    "price": item.get("price"),
                    "evidence_ids": [
                        local_evidence[key]
                        for key in cast(list[str], item.get("evidence_keys", []))
                        if key in local_evidence
                    ],
                }
            )

        activity_values = {
            "source_reported_comment_count": row["source_reported_comment_count"],
            "distinct_comments_seen": row["distinct_comments_seen"],
            "distinct_known_commenters_seen": row["distinct_known_commenters_seen"],
            "coverage": row["coverage"],
        }
        activity = (
            activity_values
            if any(value is not None for value in activity_values.values())
            else None
        )
        result_observations.append(
            {
                "id": observation_id,
                "run_id": row["run_id"],
                "capture_id": row["capture_id"],
                "observed_at": row["observed_at"],
                "access": row["access"],
                "activity": activity,
                "items": result_items,
                "evidence_ids": evidence_ids_by_observation.get(observation_id, []),
            }
        )

    return {
        "id": post["id"],
        "group_id": post["group_id"],
        "source_post_key": post["source_post_key"],
        "permalink": post["permalink"],
        "seller_id": post["seller_id"],
        "posted_at": post["posted_at"],
        "observations": result_observations,
    }


def read_evidence(conn: Connection, context: ReadContext, evidence_id: UUID) -> dict[str, Any]:
    """Return scoped evidence metadata and its media descriptor, if captured."""
    _authorize(conn, context, "read")
    row = _evidence_row(conn, context, evidence_id)
    media = None
    if row["media_state"] == "captured":
        required_media_values = (
            row["sha256"],
            row["mime"],
            row["bytes"],
            row["width"],
            row["height"],
        )
        if row["media_asset_id"] is None or any(value is None for value in required_media_values):
            raise MediaIntegrityFailure()
        media = {
            "mime": row["mime"],
            "width": row["width"],
            "height": row["height"],
            "bytes": row["bytes"],
            "sha256": row["sha256"],
            "href": f"/v1/evidence/{row['id']}/media",
        }
    return {
        "id": row["id"],
        "post_id": row["post_id"],
        "observation_id": row["observation_id"],
        "item_id": row["item_id"],
        "local_key": row["local_key"],
        "role": row["role"],
        "source_span": row["source_span"],
        "comment_version_id": row["comment_version_id"],
        "captured_at": row["captured_at"],
        "permalink": row["permalink"],
        "media_state": row["media_state"],
        "media_reason": row["media_reason"],
        "media": media,
    }


def read_evidence_media(conn: Connection, context: ReadContext, evidence_id: UUID) -> MediaContent:
    """Return media only after rechecking scope and verifying content-addressed bytes."""
    _authorize(conn, context, "media")
    row = _evidence_row(conn, context, evidence_id)
    if row["media_state"] != "captured":
        raise MediaNotAvailable()
    required_media_values = (row["sha256"], row["mime"], row["bytes"], row["width"], row["height"])
    if row["media_asset_id"] is None or any(value is None for value in required_media_values):
        raise MediaIntegrityFailure()

    configured_root = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
    if not configured_root:
        raise MediaStorageUnavailable()
    try:
        file_path = media_path(Path(configured_root).resolve(), row["sha256"], row["mime"])
    except (OSError, ValueError) as error:
        raise MediaIntegrityFailure() from error
    if file_path.is_symlink() or not file_path.is_file():
        raise MediaIntegrityFailure()
    try:
        data = file_path.read_bytes()
    except FileNotFoundError as error:
        raise MediaIntegrityFailure() from error
    except OSError as error:
        raise MediaStorageUnavailable() from error
    if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise MediaIntegrityFailure()
    return MediaContent(data=data, mime=cast(str, row["mime"]))
