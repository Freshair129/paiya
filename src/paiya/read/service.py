"""Scope-checked read operations, independent of FastAPI and transport details."""

import base64
import binascii
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Connection, and_, case, func, select

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
from paiya.store.tables import (
    capture_gaps,
    capture_runs,
    comment_versions,
    comments,
    current_items,
    evidence,
    jobs,
    listing_items,
    media_assets,
    observations,
    posts,
    price_facts,
    sale_events,
    status_facts,
)


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

    latest_payload = cast(Mapping[str, Any], observed[-1]["normalized_json"]["payload"])
    has_unresolved_items = any(
        item.get("source_item_key") is None
        for item in cast(list[Mapping[str, Any]], latest_payload.get("items", []))
    )

    last_observation = observations.alias("current_item_last_observation")
    status_event = sale_events.alias("current_item_status_event")
    status_event_fact = status_facts.alias("current_item_status_event_fact")
    status_event_observation = observations.alias("current_item_status_event_observation")
    current_status_fact = status_facts.alias("current_item_status_fact")
    current_status_observation = observations.alias("current_item_status_observation")
    current_status_evidence = evidence.alias("current_item_status_evidence")
    ask_fact = price_facts.alias("current_item_ask_fact")
    ask_observation = observations.alias("current_item_ask_observation")
    ask_evidence = evidence.alias("current_item_ask_evidence")

    current_rows = (
        conn.execute(
            select(
                listing_items.c.id.label("item_id"),
                listing_items.c.source_item_key,
                case(
                    (status_event_observation.c.id.is_not(None), status_event.c.sale_status),
                    else_=None,
                ).label("sale_status"),
                case(
                    (
                        status_event_observation.c.id.is_not(None),
                        func.coalesce(
                            status_event.c.effective_at,
                            status_event_observation.c.observed_at,
                        ),
                    ),
                    else_=None,
                ).label("status_since"),
                current_status_evidence.c.id.label("status_evidence_id"),
                current_items.c.last_success_at,
                current_items.c.needs_review,
                ask_fact.c.amount.label("ask_amount"),
                ask_fact.c.currency.label("ask_currency"),
                ask_fact.c.unit.label("ask_unit"),
                ask_evidence.c.id.label("ask_evidence_id"),
            )
            .select_from(
                current_items.join(
                    listing_items,
                    and_(
                        current_items.c.item_id == listing_items.c.id,
                        current_items.c.post_id == listing_items.c.post_id,
                    ),
                )
                .join(
                    last_observation,
                    and_(
                        current_items.c.last_observation_id == last_observation.c.id,
                        current_items.c.post_id == last_observation.c.post_id,
                    ),
                )
                .outerjoin(
                    status_event,
                    and_(
                        current_items.c.status_event_id == status_event.c.id,
                        current_items.c.item_id == status_event.c.item_id,
                        current_items.c.post_id == status_event.c.post_id,
                    ),
                )
                .outerjoin(
                    status_event_fact,
                    and_(
                        status_event.c.status_fact_id == status_event_fact.c.id,
                        status_event.c.item_id == status_event_fact.c.item_id,
                        status_event.c.post_id == status_event_fact.c.post_id,
                        status_event_fact.c.validation_result == "confirmed",
                    ),
                )
                .outerjoin(
                    status_event_observation,
                    and_(
                        status_event_fact.c.observation_id == status_event_observation.c.id,
                        status_event_observation.c.group_id == context.group_id,
                        status_event_observation.c.grant_id == context.grant_id,
                    ),
                )
                .outerjoin(
                    current_status_fact,
                    and_(
                        current_items.c.status_fact_id == current_status_fact.c.id,
                        current_items.c.item_id == current_status_fact.c.item_id,
                        current_items.c.post_id == current_status_fact.c.post_id,
                        current_status_fact.c.validation_result == "confirmed",
                    ),
                )
                .outerjoin(
                    current_status_observation,
                    and_(
                        current_status_fact.c.observation_id == current_status_observation.c.id,
                        current_status_observation.c.group_id == context.group_id,
                        current_status_observation.c.grant_id == context.grant_id,
                    ),
                )
                .outerjoin(
                    current_status_evidence,
                    and_(
                        current_status_fact.c.evidence_id == current_status_evidence.c.id,
                        current_status_fact.c.observation_id
                        == current_status_evidence.c.observation_id,
                        current_status_evidence.c.grant_id == context.grant_id,
                    ),
                )
                .outerjoin(
                    ask_fact,
                    and_(
                        current_items.c.ask_fact_id == ask_fact.c.id,
                        current_items.c.item_id == ask_fact.c.item_id,
                        current_items.c.post_id == ask_fact.c.post_id,
                        ask_fact.c.price_kind == "ask",
                    ),
                )
                .outerjoin(
                    ask_observation,
                    and_(
                        ask_fact.c.observation_id == ask_observation.c.id,
                        ask_observation.c.group_id == context.group_id,
                        ask_observation.c.grant_id == context.grant_id,
                    ),
                )
                .outerjoin(
                    ask_evidence,
                    and_(
                        ask_fact.c.evidence_id == ask_evidence.c.id,
                        ask_fact.c.observation_id == ask_evidence.c.observation_id,
                        ask_evidence.c.grant_id == context.grant_id,
                    ),
                )
            )
            .where(
                current_items.c.post_id == post_id,
                listing_items.c.source_item_key.is_not(None),
                last_observation.c.group_id == context.group_id,
                last_observation.c.grant_id == context.grant_id,
            )
            .order_by(listing_items.c.source_item_key)
        )
        .mappings()
        .all()
    )

    current_item_results: list[dict[str, Any]] = []
    for row in current_rows:
        ask = None
        if row["ask_amount"] is not None and row["ask_evidence_id"] is not None:
            ask = {
                "amount": str(row["ask_amount"]),
                "currency": row["ask_currency"],
                "unit": row["ask_unit"],
                "evidence_id": row["ask_evidence_id"],
            }
        current_item_results.append(
            {
                "item_id": row["item_id"],
                "source_item_key": row["source_item_key"],
                "sale_status": row["sale_status"] or "unknown",
                "status_since": row["status_since"],
                "status_evidence_id": row["status_evidence_id"],
                "last_success_at": row["last_success_at"],
                "needs_review": row["needs_review"],
                "ask": ask,
            }
        )

    return {
        "id": post["id"],
        "group_id": post["group_id"],
        "source_post_key": post["source_post_key"],
        "permalink": post["permalink"],
        "seller_id": post["seller_id"],
        "posted_at": post["posted_at"],
        "has_unresolved_items": has_unresolved_items,
        "current_items": current_item_results,
        "observations": result_observations,
    }


def read_post_timeline(
    conn: Connection,
    context: ReadContext,
    post_id: UUID,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return a read-context-scoped timeline computed from a post's evidence rows."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    _authorize(conn, context, "read")

    post = conn.execute(
        select(posts.c.id).where(posts.c.id == post_id, posts.c.group_id == context.group_id)
    ).scalar_one_or_none()
    if post is None:
        raise NotFound()

    observed = (
        conn.execute(
            select(
                observations.c.id,
                observations.c.observed_at,
                observations.c.access,
                observations.c.source_reported_comment_count,
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

    events: list[dict[str, Any]] = []

    status_rows = (
        conn.execute(
            select(
                sale_events.c.id,
                sale_events.c.item_id,
                sale_events.c.sale_status,
                sale_events.c.effective_at,
                status_facts.c.observation_id,
                status_facts.c.evidence_id,
                observations.c.observed_at,
            )
            .select_from(
                sale_events.join(
                    status_facts, sale_events.c.status_fact_id == status_facts.c.id
                ).join(observations, status_facts.c.observation_id == observations.c.id)
            )
            .where(
                sale_events.c.post_id == post_id,
                status_facts.c.post_id == post_id,
                observations.c.post_id == post_id,
                observations.c.group_id == context.group_id,
                observations.c.grant_id == context.grant_id,
            )
            .order_by(sale_events.c.item_id, sale_events.c.seq)
        )
        .mappings()
        .all()
    )
    previous_status_by_item: dict[UUID, str] = {}
    for row in status_rows:
        item_id = cast(UUID, row["item_id"])
        status = cast(str, row["sale_status"])
        observation_id = cast(UUID, row["observation_id"])
        observed_at = cast(datetime, row["observed_at"])
        effective_at = cast(datetime | None, row["effective_at"])
        events.append(
            _timeline_event(
                "status_changed",
                (cast(UUID, row["id"]),),
                effective_at or observed_at,
                observation_id,
                {
                    "item_id": item_id,
                    "from": previous_status_by_item.get(item_id),
                    "to": status,
                    "evidence_id": row["evidence_id"],
                },
            )
        )
        previous_status_by_item[item_id] = status

    price_rows = (
        conn.execute(
            select(
                price_facts.c.id,
                price_facts.c.item_id,
                price_facts.c.observation_id,
                price_facts.c.evidence_id,
                price_facts.c.amount,
                observations.c.observed_at,
            )
            .select_from(
                price_facts.join(observations, price_facts.c.observation_id == observations.c.id)
            )
            .where(
                price_facts.c.post_id == post_id,
                price_facts.c.item_id.is_not(None),
                price_facts.c.price_kind == "ask",
                observations.c.post_id == post_id,
                observations.c.group_id == context.group_id,
                observations.c.grant_id == context.grant_id,
            )
            .order_by(
                price_facts.c.item_id,
                observations.c.observed_at,
                observations.c.id,
                price_facts.c.id,
            )
        )
        .mappings()
        .all()
    )
    previous_price_by_item: dict[UUID, Mapping[str, Any]] = {}
    for row in price_rows:
        item_id = cast(UUID, row["item_id"])
        previous = previous_price_by_item.get(item_id)
        if previous is not None and previous["amount"] != row["amount"]:
            events.append(
                _timeline_event(
                    "price_changed",
                    (cast(UUID, row["id"]),),
                    cast(datetime, row["observed_at"]),
                    cast(UUID, row["observation_id"]),
                    {
                        "item_id": item_id,
                        "from": str(previous["amount"]),
                        "to": str(row["amount"]),
                        "evidence_id": row["evidence_id"],
                    },
                )
            )
        previous_price_by_item[item_id] = cast(Mapping[str, Any], row)

    comment_rows = (
        conn.execute(
            select(
                comments.c.id.label("comment_id"),
                comment_versions.c.id.label("version_id"),
                comment_versions.c.observation_id,
                comment_versions.c.role,
                comment_versions.c.body_hash,
                observations.c.observed_at,
            )
            .select_from(
                comments.join(
                    comment_versions, comments.c.id == comment_versions.c.comment_id
                ).join(observations, comment_versions.c.observation_id == observations.c.id)
            )
            .where(
                comments.c.post_id == post_id,
                comments.c.source_comment_key.is_not(None),
                comment_versions.c.post_id == post_id,
                observations.c.post_id == post_id,
                observations.c.group_id == context.group_id,
                observations.c.grant_id == context.grant_id,
            )
            .order_by(
                observations.c.observed_at,
                observations.c.id,
                comments.c.id,
                comment_versions.c.id,
            )
        )
        .mappings()
        .all()
    )
    previous_body_hash_by_comment: dict[UUID, str] = {}
    observations_with_new_comments: set[UUID] = set()
    for row in comment_rows:
        comment_id = cast(UUID, row["comment_id"])
        version_id = cast(UUID, row["version_id"])
        observation_id = cast(UUID, row["observation_id"])
        body_hash = cast(str, row["body_hash"])
        previous_body_hash = previous_body_hash_by_comment.get(comment_id)
        if previous_body_hash is None:
            events.append(
                _timeline_event(
                    "comment_added",
                    (comment_id, version_id),
                    cast(datetime, row["observed_at"]),
                    observation_id,
                    {"comment_id": comment_id, "role": row["role"]},
                )
            )
            observations_with_new_comments.add(observation_id)
        elif previous_body_hash != body_hash:
            events.append(
                _timeline_event(
                    "comment_edited",
                    (comment_id, version_id),
                    cast(datetime, row["observed_at"]),
                    observation_id,
                    {"comment_id": comment_id},
                )
            )
        previous_body_hash_by_comment[comment_id] = body_hash

    previous_count: int | None = None
    previous_observation_id: UUID | None = None
    previous_access: str | None = None
    for row in observed:
        observation_id = cast(UUID, row["id"])
        observed_at = cast(datetime, row["observed_at"])
        access = cast(str, row["access"])
        if previous_observation_id is not None and access != previous_access:
            events.append(
                _timeline_event(
                    "access_changed",
                    (observation_id,),
                    observed_at,
                    observation_id,
                    {"from": previous_access, "to": access},
                )
            )
        previous_access = access
        previous_observation_id = observation_id

        count = cast(int | None, row["source_reported_comment_count"])
        if count is None:
            continue
        if (
            previous_count is not None
            and count != previous_count
            and observation_id not in observations_with_new_comments
        ):
            events.append(
                _timeline_event(
                    "comment_count_changed",
                    (observation_id,),
                    observed_at,
                    observation_id,
                    {"from": previous_count, "to": count},
                )
            )
        previous_count = count

    if cursor is not None:
        cursor_key = _decode_timeline_cursor(cursor)
        events = [event for event in events if _timeline_event_key(event) > cursor_key]

    events.sort(key=_timeline_event_key)
    has_more = len(events) > limit
    page = events[:limit]
    next_cursor = (
        _encode_timeline_cursor(_timeline_event_key(page[-1])) if has_more and page else None
    )
    page_rows: list[dict[str, Any]] = []
    for event in page:
        page_row = {key: value for key, value in event.items() if key != "_at"}
        page_row["at"] = _timeline_timestamp(cast(datetime, event["_at"]))
        page_rows.append(page_row)
    return {"post_id": post_id, "events": page_rows, "next_cursor": next_cursor}


def _timeline_event(
    kind: str,
    source_ids: tuple[UUID, ...],
    at: datetime,
    observation_id: UUID,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    source_key = ":".join(str(source_id) for source_id in source_ids)
    event_id = uuid5(NAMESPACE_URL, f"paiya:post-timeline:{kind}:{source_key}")
    return {
        "id": event_id,
        "kind": kind,
        "_at": at.astimezone(UTC),
        "observation_id": observation_id,
        **fields,
    }


def _timeline_event_key(event: Mapping[str, Any]) -> tuple[datetime, str, str]:
    return (
        cast(datetime, event["_at"]),
        cast(str, event["kind"]),
        str(event["id"]),
    )


def _timeline_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _encode_timeline_cursor(key: tuple[datetime, str, str]) -> str:
    at, kind, event_id = key
    content = json.dumps(
        {"at": _timeline_timestamp(at), "kind": kind, "id": event_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(content).decode("ascii").rstrip("=")


def _decode_timeline_cursor(cursor: str) -> tuple[datetime, str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        content = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        value = json.loads(content)
        if not isinstance(value, dict) or set(value) != {"at", "kind", "id"}:
            raise ValueError
        at_value = value["at"]
        kind = value["kind"]
        event_id = value["id"]
        if (
            not isinstance(at_value, str)
            or not isinstance(kind, str)
            or not isinstance(event_id, str)
        ):
            raise ValueError
        at = datetime.fromisoformat(at_value.replace("Z", "+00:00"))
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError
        return at.astimezone(UTC), kind, str(UUID(event_id))
    except (
        ValueError,
        TypeError,
        UnicodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("cursor is invalid") from error


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


def read_group_runs(
    conn: Connection,
    context: ReadContext,
    group_id: UUID,
    limit: int = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Read newest run summaries and gaps within the trusted fixture context."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if group_id != context.group_id:
        raise NotFound()
    _authorize(conn, context, "read")

    run_query = (
        select(
            capture_runs.c.id,
            jobs.c.kind,
            jobs.c.logical_key,
            jobs.c.due_at,
            capture_runs.c.started_at,
            capture_runs.c.ended_at,
            capture_runs.c.coverage,
            capture_runs.c.partial_reasons,
            capture_runs.c.stop_reason,
            capture_runs.c.cards_seen,
            capture_runs.c.cards_parsed,
            capture_runs.c.session_result,
            capture_runs.c.sort_result,
            capture_runs.c.error_code,
        )
        .select_from(capture_runs.join(jobs, jobs.c.id == capture_runs.c.job_id))
        .where(
            capture_runs.c.group_id == context.group_id,
            capture_runs.c.grant_id == context.grant_id,
        )
    )
    if cursor is not None:
        cursor_started_at, cursor_id = _decode_run_cursor(cursor)
        run_query = run_query.where(
            (capture_runs.c.started_at < cursor_started_at)
            | ((capture_runs.c.started_at == cursor_started_at) & (capture_runs.c.id < cursor_id))
        )
    run_rows = (
        conn.execute(
            run_query.order_by(capture_runs.c.started_at.desc(), capture_runs.c.id.desc()).limit(
                limit + 1
            )
        )
        .mappings()
        .all()
    )
    has_more = len(run_rows) > limit
    page = run_rows[:limit]
    runs = [
        {
            "run_id": row["id"],
            "job_kind": row["kind"],
            "scheduled_for": _scheduled_for(row["kind"], row["logical_key"], row["due_at"]),
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "coverage": row["coverage"],
            "partial_reasons": row["partial_reasons"],
            "stop_reason": row["stop_reason"],
            "cards_seen": row["cards_seen"],
            "cards_parsed": row["cards_parsed"],
            "session_result": row["session_result"],
            "sort_result": row["sort_result"],
            "error_code": row["error_code"],
        }
        for row in page
    ]

    gaps: list[dict[str, Any]] = []
    if page:
        range_start = min(
            _scheduled_for(row["kind"], row["logical_key"], row["due_at"]) for row in page
        )
        range_end = max(cast(datetime, row["ended_at"] or row["started_at"]) for row in page)
        gap_rows = (
            conn.execute(
                select(
                    capture_gaps.c.gap_from,
                    capture_gaps.c.gap_to,
                    capture_gaps.c.reason,
                )
                .where(
                    capture_gaps.c.group_id == context.group_id,
                    capture_gaps.c.gap_from <= range_end,
                    capture_gaps.c.gap_to >= range_start,
                )
                .order_by(capture_gaps.c.gap_from.desc(), capture_gaps.c.id.desc())
            )
            .mappings()
            .all()
        )
        gaps = [
            {"gap_from": row["gap_from"], "gap_to": row["gap_to"], "reason": row["reason"]}
            for row in gap_rows
        ]

    next_cursor = _encode_run_cursor(page[-1]["started_at"], page[-1]["id"]) if has_more else None
    return {"group_id": context.group_id, "runs": runs, "gaps": gaps, "next_cursor": next_cursor}


def _encode_run_cursor(started_at: datetime, run_id: UUID) -> str:
    content = json.dumps(
        {"started_at": started_at.astimezone(UTC).isoformat(), "run_id": str(run_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(content).decode("ascii").rstrip("=")


def _scheduled_for(kind: str, logical_key: str, due_at: datetime) -> datetime:
    if kind == "discover":
        parts = logical_key.split(":", 2)
        if len(parts) == 3 and parts[0] == "discover":
            try:
                return datetime.fromisoformat(parts[2].replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                pass
    return due_at


def _decode_run_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if set(value) != {"started_at", "run_id"}:
            raise ValueError
        started_at = datetime.fromisoformat(value["started_at"])
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError
        return started_at.astimezone(UTC), UUID(value["run_id"])
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("cursor is invalid") from error
