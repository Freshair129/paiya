"""Persistence and replayable listing-item projections for extraction tags."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from paiya.extract.rules import (
    RULE_VERSION,
    extract_condition,
    extract_model,
    extract_vram,
    extract_warranty_state,
    extract_warranty_until,
    warranty_base_date,
)
from paiya.status.evidence import select_evidence_for_fact
from paiya.store.tables import (
    comment_versions,
    comments,
    evidence,
    listing_items,
    observations,
    posts,
    sellers,
    tags,
)

TagCandidate = tuple[str, str | None, str | None]


def _precision_by_comment(payload: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for comment in cast(Sequence[Mapping[str, Any]], payload.get("comments", [])):
        comment_key = comment.get("source_comment_key")
        if comment_key is None:
            continue
        source_time = cast(Mapping[str, Any] | None, comment.get("source_time"))
        result[cast(str, comment_key)] = (
            cast(str, source_time.get("precision", "unknown"))
            if source_time is not None
            else "unknown"
        )
    return result


def _load_evidence_context(
    conn: Connection,
    observation_id: UUID,
    local_keys: set[str],
    payload: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[UUID, Mapping[str, Any]], dict[str, str]]:
    if local_keys:
        evidence_rows = (
            conn.execute(
                select(
                    evidence.c.id,
                    evidence.c.local_key,
                    evidence.c.role,
                    evidence.c.source_span,
                    evidence.c.comment_version_id,
                ).where(
                    evidence.c.observation_id == observation_id,
                    evidence.c.local_key.in_(local_keys),
                )
            )
            .mappings()
            .all()
        )
    else:
        evidence_rows = []
    evidence_by_key = {
        cast(str, row["local_key"]): cast(Mapping[str, Any], row) for row in evidence_rows
    }

    comment_rows = (
        conn.execute(
            select(
                comment_versions.c.id,
                comment_versions.c.role,
                comment_versions.c.actor_key,
                comment_versions.c.source_time,
                comments.c.source_comment_key,
            )
            .select_from(
                comment_versions.join(comments, comment_versions.c.comment_id == comments.c.id)
            )
            .where(comment_versions.c.observation_id == observation_id)
        )
        .mappings()
        .all()
    )
    comments_by_version = {
        cast(UUID, row["id"]): cast(Mapping[str, Any], row) for row in comment_rows
    }
    return evidence_by_key, comments_by_version, _precision_by_comment(payload)


def _seller_actor_key(conn: Connection, post_id: UUID) -> str | None:
    return cast(
        str | None,
        conn.execute(
            select(sellers.c.external_actor_key)
            .select_from(posts.outerjoin(sellers, posts.c.seller_id == sellers.c.id))
            .where(posts.c.id == post_id)
        ).scalar_one(),
    )


def _quote_matches(
    key: str,
    quote: str,
    expected: str,
    *,
    model_text: str,
    base_date: date,
) -> bool:
    if key == "model_key":
        candidate = extract_model(quote)
    elif key == "vram_gb":
        model_result = extract_model(model_text)
        candidate = extract_vram(quote, model_result)
    elif key == "warranty":
        candidate, _until = extract_warranty_state(quote, base_date)
    elif key == "warranty_until":
        candidate = extract_warranty_until(quote, base_date)
    elif key == "condition":
        candidate = extract_condition(quote, warranty_none=expected == "new_no_warranty")
    else:
        raise ValueError(f"Unsupported extraction key: {key}")
    return candidate is not None and candidate.reason is None and candidate.value == expected


def _tag_candidates(
    item: Mapping[str, Any],
    base_date: date,
) -> list[TagCandidate]:
    model_text_value = item.get("model")
    condition_text_value = item.get("condition_text")
    model_text = model_text_value if isinstance(model_text_value, str) else ""
    condition_text = condition_text_value if isinstance(condition_text_value, str) else ""
    warranty_result, warranty_until_result = extract_warranty_state(condition_text, base_date)

    results = (
        ("model_key", extract_model(model_text)),
        ("vram_gb", extract_vram(model_text, extract_model(model_text))),
        ("warranty", warranty_result),
        ("warranty_until", warranty_until_result),
        ("condition", extract_condition(condition_text)),
    )
    return [(key, result.value, result.reason) for key, result in results if result is not None]


def _first_evidence_row(
    evidence_keys: Sequence[str], evidence_by_key: Mapping[str, Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    return next((evidence_by_key[key] for key in evidence_keys if key in evidence_by_key), None)


def _matching_evidence(
    key: str,
    expected: str,
    evidence_keys: Sequence[str],
    evidence_by_key: Mapping[str, Mapping[str, Any]],
    comments_by_version: Mapping[UUID, Mapping[str, Any]],
    precision_by_comment: Mapping[str, str],
    seller_actor_key: str | None,
    *,
    model_text: str,
    base_date: date,
) -> tuple[Mapping[str, Any] | None, bool]:
    saw_eligible_evidence = False
    for evidence_key in evidence_keys:
        selected = select_evidence_for_fact(
            [evidence_key],
            evidence_by_key,
            comments_by_version,
            precision_by_comment,
            seller_actor_key,
        )
        if selected is None:
            continue
        evidence_row, eligible, _reason, _effective_at, _precision = selected
        if not eligible:
            continue
        saw_eligible_evidence = True
        source_span = cast(Mapping[str, Any] | None, evidence_row.get("source_span"))
        quote = source_span.get("quote") if source_span is not None else None
        if isinstance(quote, str) and _quote_matches(
            key,
            quote,
            expected,
            model_text=model_text,
            base_date=base_date,
        ):
            return evidence_row, saw_eligible_evidence
    return None, saw_eligible_evidence


def _reason_for_unmatched_value(
    evidence_keys: Sequence[str],
    evidence_by_key: Mapping[str, Mapping[str, Any]],
    comments_by_version: Mapping[UUID, Mapping[str, Any]],
    precision_by_comment: Mapping[str, str],
    seller_actor_key: str | None,
) -> str:
    selected = select_evidence_for_fact(
        evidence_keys,
        evidence_by_key,
        comments_by_version,
        precision_by_comment,
        seller_actor_key,
    )
    if selected is not None and selected[2] is not None:
        return selected[2]
    return "no_eligible_evidence"


def _values_for_item(
    item: Mapping[str, Any],
    evidence_keys: Sequence[str],
    evidence_by_key: Mapping[str, Mapping[str, Any]],
    comments_by_version: Mapping[UUID, Mapping[str, Any]],
    precision_by_comment: Mapping[str, str],
    seller_actor_key: str | None,
    base_date: date,
) -> list[dict[str, Any]]:
    first_evidence = _first_evidence_row(evidence_keys, evidence_by_key)
    if first_evidence is None:
        return []

    model_text_value = item.get("model")
    model_text = model_text_value if isinstance(model_text_value, str) else ""
    rows: list[dict[str, Any]] = []
    confirmed_warranty: str | None = None

    for key, value, lexical_reason in _tag_candidates(item, base_date):
        if key == "condition" and value == "new" and confirmed_warranty == "none":
            value = "new_no_warranty"
        evidence_id = cast(UUID, first_evidence["id"])
        if lexical_reason is not None:
            rows.append(
                {
                    "key": key,
                    "value": None,
                    "evidence_id": evidence_id,
                    "validation_result": "rejected",
                    "reason": lexical_reason,
                }
            )
            continue

        if value is None:
            continue
        selected_evidence, saw_eligible_evidence = _matching_evidence(
            key,
            value,
            evidence_keys,
            evidence_by_key,
            comments_by_version,
            precision_by_comment,
            seller_actor_key,
            model_text=model_text,
            base_date=base_date,
        )
        if selected_evidence is not None:
            rows.append(
                {
                    "key": key,
                    "value": value,
                    "evidence_id": selected_evidence["id"],
                    "validation_result": "confirmed",
                    "reason": None,
                }
            )
            if key == "warranty":
                confirmed_warranty = value
        else:
            rows.append(
                {
                    "key": key,
                    "value": value,
                    "evidence_id": evidence_id,
                    "validation_result": "rejected",
                    "reason": (
                        "quote_mismatch"
                        if saw_eligible_evidence
                        else _reason_for_unmatched_value(
                            evidence_keys,
                            evidence_by_key,
                            comments_by_version,
                            precision_by_comment,
                            seller_actor_key,
                        )
                    ),
                }
            )
    return rows


def _projection_for_item(conn: Connection, item_id: UUID) -> dict[str, Any]:
    rows = (
        conn.execute(
            select(
                tags.c.key,
                tags.c.value,
                tags.c.observation_id,
                observations.c.observed_at,
            )
            .select_from(tags.join(observations, tags.c.observation_id == observations.c.id))
            .where(
                tags.c.item_id == item_id,
                tags.c.method == "rule",
                tags.c.rule_version == RULE_VERSION,
                tags.c.validation_result == "confirmed",
            )
            .order_by(observations.c.observed_at.desc(), observations.c.id.desc())
        )
        .mappings()
        .all()
    )

    model_by_observation: dict[UUID, str] = {}
    warranty_by_observation: dict[UUID, str] = {}
    latest_by_key: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        observation_id = cast(UUID, row["observation_id"])
        key = cast(str, row["key"])
        value = cast(str, row["value"])
        if key == "model_key":
            model_by_observation.setdefault(observation_id, value)
        elif key == "warranty":
            warranty_by_observation.setdefault(observation_id, value)
        latest_by_key.setdefault(key, cast(Mapping[str, Any], row))

    model_row = latest_by_key.get("model_key")
    model_key = cast(str | None, model_row["value"]) if model_row is not None else None
    condition_row = latest_by_key.get("condition")
    condition = cast(str | None, condition_row["value"]) if condition_row is not None else None
    warranty_row = latest_by_key.get("warranty")
    warranty = cast(str | None, warranty_row["value"]) if warranty_row is not None else None

    warranty_until: date | None = None
    if warranty is not None:
        for row in rows:
            if row["key"] != "warranty_until":
                continue
            observation_id = cast(UUID, row["observation_id"])
            if warranty_by_observation.get(observation_id) != warranty:
                continue
            year_text, month_text = cast(str, row["value"]).split("-", maxsplit=1)
            warranty_until = date(int(year_text), int(month_text), 1)
            break

    vram_gb: int | None = None
    if model_key is not None:
        for row in rows:
            if row["key"] != "vram_gb":
                continue
            observation_id = cast(UUID, row["observation_id"])
            if model_by_observation.get(observation_id) == model_key:
                vram_gb = int(cast(str, row["value"]))
                break

    return {
        "model_key": model_key,
        "vram_gb": vram_gb,
        "condition": condition,
        "warranty": warranty,
        "warranty_until": warranty_until,
    }


def _recompute_item(conn: Connection, item_id: UUID, *, already_locked: bool = False) -> bool:
    query = select(
        listing_items.c.model_key,
        listing_items.c.vram_gb,
        listing_items.c.condition,
        listing_items.c.warranty,
        listing_items.c.warranty_until,
    ).where(listing_items.c.id == item_id)
    if not already_locked:
        query = query.with_for_update()
    current = conn.execute(query).mappings().one_or_none()
    if current is None:
        return False

    projection = _projection_for_item(conn, item_id)
    if all(current[key] == value for key, value in projection.items()):
        return False
    conn.execute(update(listing_items).where(listing_items.c.id == item_id).values(**projection))
    return True


def create_tags_and_rebuild_listing_items(
    conn: Connection,
    *,
    observation_id: UUID,
    post_id: UUID,
    payload: Mapping[str, Any],
    item_ids: Mapping[str, UUID],
) -> tuple[int, int]:
    """Insert this observation's tags and refresh each referenced item in its transaction."""
    if payload.get("access") != "ok":
        return 0, 0

    observed_at = cast(
        datetime,
        conn.execute(
            select(observations.c.observed_at).where(observations.c.id == observation_id)
        ).scalar_one(),
    )
    posted_at_value = payload.get("posted_at")
    posted_at = cast(Mapping[str, object] | None, posted_at_value)
    base_date = warranty_base_date(posted_at, observed_at)

    entries = cast(Sequence[Mapping[str, Any]], payload.get("items", []))
    keyed_entries = [entry for entry in entries if entry.get("source_item_key") is not None]
    local_keys = {
        evidence_key
        for entry in keyed_entries
        for evidence_key in cast(Sequence[str], entry.get("evidence_keys", []))
    }
    evidence_by_key, comments_by_version, precision_by_comment = _load_evidence_context(
        conn, observation_id, local_keys, payload
    )
    seller_actor_key = _seller_actor_key(conn, post_id)

    prepared: list[tuple[UUID, Sequence[str], Mapping[str, Any], list[dict[str, Any]]]] = []
    for entry in keyed_entries:
        source_item_key = cast(str, entry["source_item_key"])
        item_id = item_ids.get(source_item_key)
        evidence_keys = cast(Sequence[str], entry.get("evidence_keys", []))
        if item_id is None or not evidence_keys:
            continue
        prepared.append(
            (
                item_id,
                evidence_keys,
                entry,
                _values_for_item(
                    entry,
                    evidence_keys,
                    evidence_by_key,
                    comments_by_version,
                    precision_by_comment,
                    seller_actor_key,
                    base_date,
                ),
            )
        )

    touched_item_ids = sorted({item_id for item_id, _keys, _entry, _rows in prepared}, key=str)
    if touched_item_ids:
        conn.execute(
            select(listing_items.c.id)
            .where(listing_items.c.id.in_(touched_item_ids))
            .order_by(listing_items.c.id)
            .with_for_update()
        ).all()

    tags_added = 0
    for item_id, _evidence_keys, _entry, rows in prepared:
        for row in rows:
            result = conn.execute(
                pg_insert(tags)
                .values(
                    id=uuid4(),
                    observation_id=observation_id,
                    post_id=post_id,
                    item_id=item_id,
                    evidence_id=row["evidence_id"],
                    key=row["key"],
                    value=row["value"],
                    method="rule",
                    model_version=None,
                    confidence=None,
                    rule_version=RULE_VERSION,
                    validation_result=row["validation_result"],
                    reason=row["reason"],
                )
                .on_conflict_do_nothing(constraint="uq_tags_observation_item_key_method_version")
                .returning(tags.c.id)
            )
            if result.scalar_one_or_none() is not None:
                tags_added += 1

    items_changed = sum(
        _recompute_item(conn, item_id, already_locked=True) for item_id in touched_item_ids
    )
    return tags_added, items_changed


def _projection_tuple(row: Mapping[str, Any]) -> tuple[Any, Any, Any, Any, Any]:
    return (
        row["model_key"],
        row["vram_gb"],
        row["condition"],
        row["warranty"],
        row["warranty_until"],
    )


def rebuild_extractions(engine: Engine) -> dict[str, int]:
    """Fill missing extract-v1.0 tags and rebuild every listing-item projection."""
    with engine.begin() as conn:
        original_rows = (
            conn.execute(
                select(
                    listing_items.c.id,
                    listing_items.c.model_key,
                    listing_items.c.vram_gb,
                    listing_items.c.condition,
                    listing_items.c.warranty,
                    listing_items.c.warranty_until,
                )
                .order_by(listing_items.c.id)
                .with_for_update()
            )
            .mappings()
            .all()
        )
        original = {
            cast(UUID, row["id"]): _projection_tuple(cast(Mapping[str, Any], row))
            for row in original_rows
        }

        observation_rows = (
            conn.execute(
                select(
                    observations.c.id, observations.c.post_id, observations.c.normalized_json
                ).order_by(observations.c.observed_at, observations.c.id)
            )
            .mappings()
            .all()
        )
        tags_added = 0
        for observation in observation_rows:
            normalized = cast(Mapping[str, Any], observation["normalized_json"])
            payload_value = normalized.get("payload")
            if not isinstance(payload_value, Mapping):
                continue
            payload = cast(Mapping[str, Any], payload_value)
            post_id = cast(UUID, observation["post_id"])
            item_rows = (
                conn.execute(
                    select(listing_items.c.id, listing_items.c.source_item_key).where(
                        listing_items.c.post_id == post_id,
                        listing_items.c.source_item_key.is_not(None),
                    )
                )
                .mappings()
                .all()
            )
            item_ids = {
                cast(str, row["source_item_key"]): cast(UUID, row["id"]) for row in item_rows
            }
            added, _items_changed = create_tags_and_rebuild_listing_items(
                conn,
                observation_id=cast(UUID, observation["id"]),
                post_id=post_id,
                payload=payload,
                item_ids=item_ids,
            )
            tags_added += added

        all_item_ids: list[UUID] = cast(
            list[UUID],
            conn.execute(select(listing_items.c.id).order_by(listing_items.c.id).with_for_update())
            .scalars()
            .all(),
        )
        for item_id in all_item_ids:
            _recompute_item(conn, item_id, already_locked=True)

        final_rows = (
            conn.execute(
                select(
                    listing_items.c.id,
                    listing_items.c.model_key,
                    listing_items.c.vram_gb,
                    listing_items.c.condition,
                    listing_items.c.warranty,
                    listing_items.c.warranty_until,
                ).order_by(listing_items.c.id)
            )
            .mappings()
            .all()
        )
        items_changed = sum(
            original.get(cast(UUID, row["id"])) != _projection_tuple(cast(Mapping[str, Any], row))
            for row in final_rows
        )

    return {
        "observations_scanned": len(observation_rows),
        "tags_added": tags_added,
        "items_changed": items_changed,
    }
