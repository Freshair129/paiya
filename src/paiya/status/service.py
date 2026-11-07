"""Create item facts and rebuild sale/current projections during fixture ingest."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import Connection, delete, func, insert, select, update

from paiya.store.tables import (
    comment_versions,
    comments,
    current_items,
    evidence,
    listing_items,
    observations,
    posts,
    price_facts,
    sale_events,
    sellers,
    status_facts,
)

STATUS_RULE_VERSION = "status-v1.0"
STATUS_LABELS = {"เปิดขาย": "open", "ติดจอง": "reserved", "ขายแล้ว": "sold"}


def _price_amount(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _source_time_precision_by_comment(
    payload: Mapping[str, Any],
) -> dict[str, str]:
    precision_by_key: dict[str, str] = {}
    for comment in cast(Sequence[Mapping[str, Any]], payload.get("comments", [])):
        comment_key = comment.get("source_comment_key")
        if comment_key is None:
            continue
        source_time = cast(Mapping[str, Any] | None, comment.get("source_time"))
        precision_by_key[cast(str, comment_key)] = (
            cast(str, source_time.get("precision", "unknown"))
            if source_time is not None
            else "unknown"
        )
    return precision_by_key


def _evidence_eligibility(
    evidence_row: Mapping[str, Any],
    comments_by_version: Mapping[UUID, Mapping[str, Any]],
    precision_by_comment: Mapping[str, str],
    seller_actor_key: str | None,
) -> tuple[bool, str | None, datetime | None, str]:
    role = cast(str, evidence_row["role"])
    source_span = cast(Mapping[str, Any] | None, evidence_row["source_span"])
    field = source_span.get("field") if source_span is not None else None

    if role == "source_label":
        return True, None, None, "unknown"
    if role == "seller" and field == "post_text":
        return True, None, None, "unknown"

    if field != "comment_text":
        return False, "no_eligible_evidence", None, "unknown"

    comment_version_id = cast(UUID | None, evidence_row["comment_version_id"])
    comment = (
        comments_by_version.get(comment_version_id) if comment_version_id is not None else None
    )
    comment_key = cast(str | None, source_span.get("comment_key")) if source_span else None
    precision = precision_by_comment.get(comment_key, "unknown") if comment_key else "unknown"
    effective_at = cast(datetime | None, comment["source_time"]) if comment is not None else None

    if comment is None:
        return False, "no_eligible_evidence", effective_at, precision
    if role != comment["role"]:
        return False, "role_mismatch", effective_at, precision
    if seller_actor_key is None:
        return False, "seller_unknown", effective_at, precision
    if comment["actor_key"] != seller_actor_key:
        return False, "actor_mismatch", effective_at, precision
    if role == "seller" and comment["role"] == "seller":
        return True, None, effective_at, precision
    return False, "no_eligible_evidence", effective_at, precision


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
    evidence_by_key = {row["local_key"]: cast(Mapping[str, Any], row) for row in evidence_rows}

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
    return evidence_by_key, comments_by_version, _source_time_precision_by_comment(payload)


def _seller_actor_key(conn: Connection, post_id: UUID) -> str | None:
    return cast(
        str | None,
        conn.execute(
            select(sellers.c.external_actor_key)
            .select_from(posts.outerjoin(sellers, posts.c.seller_id == sellers.c.id))
            .where(posts.c.id == post_id)
        ).scalar_one(),
    )


def _create_price_fact(
    conn: Connection,
    *,
    observation_id: UUID,
    post_id: UUID,
    item_id: UUID,
    source_item_key: str,
    price: Mapping[str, Any] | None,
    evidence_keys: Sequence[str],
    evidence_by_key: Mapping[str, Mapping[str, Any]],
) -> bool:
    if price is None or not evidence_keys:
        return False
    evidence_row = evidence_by_key.get(evidence_keys[0])
    if evidence_row is None:
        return False

    price_kind = cast(str, price["kind"])
    unit = cast(str, price["unit"])
    amount_max = _price_amount(cast(str | None, price.get("amount_max")))
    conn.execute(
        insert(price_facts).values(
            id=uuid4(),
            observation_id=observation_id,
            post_id=post_id,
            item_id=item_id,
            evidence_id=evidence_row["id"],
            fact_key=f"price:{source_item_key}:{price_kind}:{unit}",
            price_kind=price_kind,
            unit=unit,
            amount=Decimal(cast(str, price["amount"])),
            amount_max=amount_max,
            currency=cast(str, price["currency"]),
        )
    )
    return True


def _create_status_fact(
    conn: Connection,
    *,
    observation_id: UUID,
    post_id: UUID,
    item_id: UUID,
    status_label: str | None,
    evidence_keys: Sequence[str],
    evidence_by_key: Mapping[str, Mapping[str, Any]],
    comments_by_version: Mapping[UUID, Mapping[str, Any]],
    precision_by_comment: Mapping[str, str],
    seller_actor_key: str | None,
) -> bool:
    if status_label is None or not evidence_keys:
        return False
    normalized_label = status_label.strip()
    sale_status = STATUS_LABELS.get(normalized_label)
    if sale_status is None:
        return False

    evidence_context = [evidence_by_key[key] for key in evidence_keys if key in evidence_by_key]
    if not evidence_context:
        return False

    selected: tuple[Mapping[str, Any], bool, str | None, datetime | None, str] | None = None
    first_result: tuple[Mapping[str, Any], bool, str | None, datetime | None, str] | None = None
    for evidence_row in evidence_context:
        decision = _evidence_eligibility(
            evidence_row, comments_by_version, precision_by_comment, seller_actor_key
        )
        result = (evidence_row, *decision)
        if first_result is None:
            first_result = result
        if decision[0]:
            selected = result
            break

    if selected is None:
        selected = first_result
    if selected is None:
        return False

    evidence_row, confirmed, reason, effective_at, precision = selected
    conn.execute(
        insert(status_facts).values(
            id=uuid4(),
            observation_id=observation_id,
            post_id=post_id,
            item_id=item_id,
            evidence_id=evidence_row["id"],
            fact_key=f"status:{normalized_label}",
            sale_status=sale_status,
            effective_at=effective_at,
            precision=precision,
            rule_version=STATUS_RULE_VERSION,
            validation_result="confirmed" if confirmed else "rejected",
            reason=None if confirmed else reason or "no_eligible_evidence",
        )
    )
    return True


def _ensure_current_item(
    conn: Connection,
    *,
    item_id: UUID,
    post_id: UUID,
    observation_id: UUID,
    observed_at: datetime,
) -> None:
    # listing_items is the stable per-item lock, including before current_items exists.
    conn.execute(
        select(listing_items.c.id)
        .where(listing_items.c.id == item_id, listing_items.c.post_id == post_id)
        .with_for_update()
    ).scalar_one()
    current = (
        conn.execute(
            select(current_items.c.last_observation_id, current_items.c.last_success_at)
            .where(current_items.c.item_id == item_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if current is None:
        conn.execute(
            insert(current_items).values(
                item_id=item_id,
                post_id=post_id,
                status_event_id=None,
                status_fact_id=None,
                ask_fact_id=None,
                shipping_fact_id=None,
                last_observation_id=observation_id,
                last_success_at=observed_at,
                needs_review=False,
            )
        )
        return

    latest_at = cast(datetime | None, current["last_success_at"])
    latest_id = cast(UUID, current["last_observation_id"])
    if latest_at is None or (observed_at, observation_id.int) > (latest_at, latest_id.int):
        conn.execute(
            update(current_items)
            .where(current_items.c.item_id == item_id)
            .values(last_observation_id=observation_id, last_success_at=observed_at)
        )


def _latest_price_fact(conn: Connection, item_id: UUID, price_kind: str) -> UUID | None:
    return cast(
        UUID | None,
        conn.execute(
            select(price_facts.c.id)
            .select_from(
                price_facts.join(observations, price_facts.c.observation_id == observations.c.id)
            )
            .where(price_facts.c.item_id == item_id, price_facts.c.price_kind == price_kind)
            .order_by(observations.c.observed_at.desc(), observations.c.id.desc())
            .limit(1)
        ).scalar_one_or_none(),
    )


def _rebuild_sale_events_and_projection(conn: Connection, item_id: UUID, post_id: UUID) -> None:
    facts = (
        conn.execute(
            select(
                status_facts.c.id,
                status_facts.c.evidence_id,
                status_facts.c.sale_status,
                status_facts.c.effective_at,
                status_facts.c.precision,
                observations.c.id.label("observation_id"),
                observations.c.observed_at,
            )
            .select_from(
                status_facts.join(observations, status_facts.c.observation_id == observations.c.id)
            )
            .where(
                status_facts.c.item_id == item_id,
                status_facts.c.validation_result == "confirmed",
            )
            .order_by(
                func.coalesce(status_facts.c.effective_at, observations.c.observed_at),
                observations.c.observed_at,
                observations.c.id,
            )
        )
        .mappings()
        .all()
    )

    conn.execute(
        update(current_items).where(current_items.c.item_id == item_id).values(status_event_id=None)
    )
    conn.execute(delete(sale_events).where(sale_events.c.item_id == item_id))

    previous_status: str | None = None
    status_event_id: UUID | None = None
    event_seq = 0
    tied_effective_statuses: dict[datetime, set[str]] = {}
    for fact in facts:
        sale_status = cast(str, fact["sale_status"])
        effective_at = cast(datetime | None, fact["effective_at"])
        if effective_at is not None:
            tied_effective_statuses.setdefault(effective_at, set()).add(sale_status)
        if previous_status is None or sale_status != previous_status:
            event_seq += 1
            cause_key = f"status:{fact['evidence_id']}"
            status_event_id = uuid5(NAMESPACE_URL, f"paiya:sale-event:{item_id}:{cause_key}")
            conn.execute(
                insert(sale_events).values(
                    id=status_event_id,
                    item_id=item_id,
                    post_id=post_id,
                    status_fact_id=fact["id"],
                    seq=event_seq,
                    sale_status=sale_status,
                    cause_key=cause_key,
                    effective_at=effective_at,
                    precision=fact["precision"],
                )
            )
        previous_status = sale_status

    status_fact_id = cast(UUID | None, facts[-1]["id"]) if facts else None
    needs_review = any(len(statuses) > 1 for statuses in tied_effective_statuses.values())
    conn.execute(
        update(current_items)
        .where(current_items.c.item_id == item_id)
        .values(
            status_event_id=status_event_id,
            status_fact_id=status_fact_id,
            ask_fact_id=_latest_price_fact(conn, item_id, "ask"),
            shipping_fact_id=_latest_price_fact(conn, item_id, "shipping"),
            needs_review=needs_review,
        )
    )


def create_facts_and_rebuild_current_items(
    conn: Connection,
    *,
    observation_id: UUID,
    post_id: UUID,
    observed_at: datetime,
    payload: Mapping[str, Any],
    item_ids: Mapping[str, UUID],
) -> None:
    """Persist facts and rebuild each keyed item projection for a successful capture."""
    if payload.get("access") != "ok":
        return

    entries = cast(Sequence[Mapping[str, Any]], payload.get("items", []))
    keyed_entries = [entry for entry in entries if entry.get("source_item_key") is not None]
    if not keyed_entries:
        return

    evidence_keys = {
        key
        for entry in keyed_entries
        for key in cast(Sequence[str], entry.get("evidence_keys", []))
    }
    evidence_by_key, comments_by_version, precision_by_comment = _load_evidence_context(
        conn, observation_id, evidence_keys, payload
    )
    seller_actor_key = _seller_actor_key(conn, post_id)

    for entry in keyed_entries:
        source_item_key = entry["source_item_key"]
        item_id = item_ids[source_item_key]
        entry_evidence_keys = cast(Sequence[str], entry.get("evidence_keys", []))
        price = cast(Mapping[str, Any] | None, entry.get("price"))
        price_created = _create_price_fact(
            conn,
            observation_id=observation_id,
            post_id=post_id,
            item_id=item_id,
            source_item_key=source_item_key,
            price=price,
            evidence_keys=entry_evidence_keys,
            evidence_by_key=evidence_by_key,
        )
        status_created = _create_status_fact(
            conn,
            observation_id=observation_id,
            post_id=post_id,
            item_id=item_id,
            status_label=cast(str | None, entry.get("source_status_label")),
            evidence_keys=entry_evidence_keys,
            evidence_by_key=evidence_by_key,
            comments_by_version=comments_by_version,
            precision_by_comment=precision_by_comment,
            seller_actor_key=seller_actor_key,
        )

        _ensure_current_item(
            conn,
            item_id=item_id,
            post_id=post_id,
            observation_id=observation_id,
            observed_at=observed_at,
        )
        if price_created or status_created:
            _rebuild_sale_events_and_projection(conn, item_id, post_id)
