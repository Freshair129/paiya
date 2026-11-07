"""Queue processing and evidence-backed validation for local-model enrichment."""

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
from sqlalchemy import Connection, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from paiya.enrich import ENRICH_RULE_VERSION
from paiya.enrich.client import (
    SYSTEM_PROMPT,
    EnrichmentResponse,
    EnrichRequestError,
    EnrichRetryableError,
    InvalidModelOutput,
    call_ollama,
)
from paiya.extract.rules import MODEL_TABLE, normalize_text, warranty_base_date
from paiya.extract.service import (
    _first_evidence_row,
    _load_evidence_context,
    _quote_matches,
    _seller_actor_key,
    recompute_listing_item_projection,
)
from paiya.scheduler.models import EnrichJobClaim
from paiya.scheduler.queue import (
    ENRICH_LEASE_MINUTES,
    cancel_enrich,
    claim_enrich_next,
    complete_enrich,
    fail_enrich,
    grant_is_readable,
    retry_enrich,
)
from paiya.settings import EnrichSettings, get_enrich_settings
from paiya.status.evidence import select_evidence_for_fact
from paiya.store.tables import jobs, listing_items, observations, tags

MAX_INPUT_CHARS = 4000
_CONDITION_VALUES = {"new", "new_no_warranty", "used"}
_WARRANTY_VALUES = {"active", "none"}
_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
_VALIDATION_RESULTS = ("confirmed", "proposed", "rejected")


@dataclass(frozen=True)
class _ObservationContext:
    observation_id: UUID
    post_id: UUID
    observed_at: datetime
    payload: Mapping[str, Any]
    item_ids: Mapping[str, UUID]
    evidence_by_key: Mapping[str, Mapping[str, Any]]
    comments_by_version: Mapping[UUID, Mapping[str, Any]]
    precision_by_comment: Mapping[str, str]
    seller_actor_key: str | None


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _build_user_content(context: _ObservationContext) -> str:
    entries_value = context.payload.get("items", [])
    if not isinstance(entries_value, Sequence):
        entries_value = []
    model_items: list[dict[str, Any]] = []
    for item_value in entries_value:
        if not isinstance(item_value, Mapping):
            continue
        source_item_key = item_value.get("source_item_key")
        evidence_keys_value = item_value.get("evidence_keys", [])
        if (
            not isinstance(source_item_key, str)
            or source_item_key not in context.item_ids
            or not isinstance(evidence_keys_value, Sequence)
            or not evidence_keys_value
        ):
            continue

        evidence_pairs: list[dict[str, str]] = []
        for evidence_key_value in evidence_keys_value:
            if not isinstance(evidence_key_value, str):
                continue
            decision = select_evidence_for_fact(
                [evidence_key_value],
                context.evidence_by_key,
                context.comments_by_version,
                context.precision_by_comment,
                context.seller_actor_key,
            )
            if decision is None or not decision[1]:
                continue
            evidence_row = decision[0]
            source_span_value = evidence_row.get("source_span")
            if not isinstance(source_span_value, Mapping):
                continue
            quote = source_span_value.get("quote")
            if not isinstance(quote, str):
                continue
            evidence_pairs.append({"evidence_local_key": evidence_key_value, "quote": quote})

        if evidence_pairs:
            model_value = item_value.get("model")
            condition_value = item_value.get("condition_text")
            model_items.append(
                {
                    "source_item_key": source_item_key,
                    "model": model_value if isinstance(model_value, str) else "",
                    "condition_text": condition_value if isinstance(condition_value, str) else "",
                    "evidence": evidence_pairs,
                }
            )

    data: dict[str, Any] = {"items": model_items}
    content = _json_text(data)
    max_user_chars = MAX_INPUT_CHARS - len(SYSTEM_PROMPT)
    while len(content) > max_user_chars:
        if model_items and model_items[-1]["evidence"]:
            model_items[-1]["evidence"].pop()
            if not model_items[-1]["evidence"]:
                model_items.pop()
        elif model_items:
            model_items.pop()
        else:
            break
        content = _json_text(data)
    return content


def _load_context(
    engine: Engine,
    claim: EnrichJobClaim,
    now: datetime,
) -> _ObservationContext | None:
    try:
        observation_id = UUID(claim.target_key)
    except ValueError:
        with engine.begin() as conn:
            fail_enrich(conn, claim, now, "invalid_observation_reference")
        return None

    with engine.connect() as conn, conn.begin():
        if not grant_is_readable(
            conn,
            group_id=claim.group_id,
            grant_id=claim.grant_id,
            now=now,
        ):
            cancel_enrich(conn, claim, now)
            return None

        observation = (
            conn.execute(
                select(
                    observations.c.post_id,
                    observations.c.group_id,
                    observations.c.grant_id,
                    observations.c.access,
                    observations.c.observed_at,
                    observations.c.normalized_json,
                ).where(observations.c.id == observation_id)
            )
            .mappings()
            .one_or_none()
        )
        if (
            observation is None
            or observation["group_id"] != claim.group_id
            or observation["grant_id"] != claim.grant_id
            or observation["access"] != "ok"
        ):
            fail_enrich(conn, claim, now, "observation_unavailable")
            return None

        normalized_value = observation["normalized_json"]
        normalized = normalized_value if isinstance(normalized_value, Mapping) else {}
        payload_value = normalized.get("payload")
        if not isinstance(payload_value, Mapping):
            fail_enrich(conn, claim, now, "observation_payload_unavailable")
            return None
        payload = cast(Mapping[str, Any], payload_value)

        item_rows = (
            conn.execute(
                select(listing_items.c.id, listing_items.c.source_item_key).where(
                    listing_items.c.post_id == observation["post_id"],
                    listing_items.c.source_item_key.is_not(None),
                )
            )
            .mappings()
            .all()
        )
        item_ids = {cast(str, row["source_item_key"]): cast(UUID, row["id"]) for row in item_rows}
        entries_value = payload.get("items", [])
        local_keys = {
            evidence_key
            for item_value in entries_value
            if isinstance(item_value, Mapping)
            for evidence_key in item_value.get("evidence_keys", [])
            if isinstance(evidence_key, str)
        }
        evidence_by_key, comments_by_version, precision_by_comment = _load_evidence_context(
            conn, observation_id, local_keys, payload
        )
        return _ObservationContext(
            observation_id=observation_id,
            post_id=cast(UUID, observation["post_id"]),
            observed_at=cast(datetime, observation["observed_at"]),
            payload=payload,
            item_ids=item_ids,
            evidence_by_key=evidence_by_key,
            comments_by_version=comments_by_version,
            precision_by_comment=precision_by_comment,
            seller_actor_key=_seller_actor_key(conn, cast(UUID, observation["post_id"])),
        )


def _is_in_vocabulary(key: str, value: str) -> bool:
    if key == "model_key":
        return value in MODEL_TABLE
    if key == "vram_gb":
        if re.fullmatch(r"\d{1,2}", value) is None or str(int(value)) != value:
            return False
        return 1 <= int(value) <= 99
    if key == "condition":
        return value in _CONDITION_VALUES
    if key == "warranty":
        return value in _WARRANTY_VALUES
    if key == "warranty_until":
        if _MONTH_PATTERN.fullmatch(value) is None:
            return False
        year_text, month_text = value.split("-", maxsplit=1)
        return 1 <= int(year_text) <= 9999 and 1 <= int(month_text) <= 12
    return False


def _dedupe_proposals(
    proposals: Sequence[Mapping[str, Any]],
) -> list[tuple[int, Mapping[str, Any]]]:
    selected: dict[tuple[str, str], tuple[int, Mapping[str, Any]]] = {}
    for index, proposal in enumerate(proposals):
        identity = (
            cast(str, proposal["source_item_key"]),
            cast(str, proposal["key"]),
        )
        existing = selected.get(identity)
        if existing is None or float(proposal["confidence"]) > float(existing[1]["confidence"]):
            selected[identity] = (index, proposal)
    return sorted(selected.values(), key=lambda value: value[0])


def _confirmed_dependencies(
    conn: Connection,
    observation_id: UUID,
) -> tuple[dict[UUID, str], dict[UUID, str]]:
    rows = (
        conn.execute(
            select(tags.c.item_id, tags.c.key, tags.c.value, tags.c.method)
            .where(
                tags.c.observation_id == observation_id,
                tags.c.key.in_(["model_key", "warranty"]),
                tags.c.validation_result == "confirmed",
                or_(
                    (tags.c.method == "rule") & (tags.c.rule_version == "extract-v1.0"),
                    (tags.c.method == "model") & (tags.c.rule_version == ENRICH_RULE_VERSION),
                ),
            )
            .order_by(case((tags.c.method == "rule", 0), else_=1))
        )
        .mappings()
        .all()
    )
    model_by_item: dict[UUID, str] = {}
    warranty_by_item: dict[UUID, str] = {}
    for row in rows:
        item_id = cast(UUID, row["item_id"])
        key = cast(str, row["key"])
        value = cast(str, row["value"])
        if key == "model_key":
            model_by_item.setdefault(item_id, value)
        elif key == "warranty":
            warranty_by_item.setdefault(item_id, value)
    return model_by_item, warranty_by_item


def _tag_for_proposal(
    proposal: Mapping[str, Any],
    context: _ObservationContext,
    entry_by_key: Mapping[str, Mapping[str, Any]],
    base_date: date,
    model_by_item: dict[UUID, str],
    warranty_by_item: dict[UUID, str],
) -> tuple[dict[str, Any] | None, bool]:
    source_item_key = cast(str, proposal["source_item_key"])
    key = cast(str, proposal["key"])
    value = cast(str, proposal["value"])
    evidence_local_key = cast(str, proposal["evidence_local_key"])
    quote = cast(str, proposal["quote"])
    confidence = float(proposal["confidence"])
    item_id = context.item_ids.get(source_item_key)
    item = entry_by_key.get(source_item_key)
    evidence_keys_value = item.get("evidence_keys", []) if item is not None else []
    evidence_keys = (
        cast(Sequence[str], evidence_keys_value)
        if isinstance(evidence_keys_value, Sequence)
        else []
    )
    first_evidence = _first_evidence_row(evidence_keys, context.evidence_by_key)
    in_vocabulary = _is_in_vocabulary(key, value)
    stored_value = value if in_vocabulary else None

    if item_id is None or item is None or not evidence_keys or first_evidence is None:
        return None, True

    evidence_row = context.evidence_by_key.get(evidence_local_key)
    if evidence_local_key not in evidence_keys or evidence_row is None:
        return (
            {
                "item_id": item_id,
                "evidence_id": first_evidence["id"],
                "key": key,
                "value": stored_value,
                "confidence": confidence,
                "validation_result": "rejected",
                "reason": "unknown_reference",
            },
            False,
        )

    decision = select_evidence_for_fact(
        [evidence_local_key],
        context.evidence_by_key,
        context.comments_by_version,
        context.precision_by_comment,
        context.seller_actor_key,
    )
    if decision is None:
        return (
            {
                "item_id": item_id,
                "evidence_id": first_evidence["id"],
                "key": key,
                "value": stored_value,
                "confidence": confidence,
                "validation_result": "rejected",
                "reason": "unknown_reference",
            },
            False,
        )
    evidence_row, eligible, reason, _effective_at, _precision = decision
    if not eligible:
        return (
            {
                "item_id": item_id,
                "evidence_id": evidence_row["id"],
                "key": key,
                "value": stored_value,
                "confidence": confidence,
                "validation_result": "rejected",
                "reason": reason or "no_eligible_evidence",
            },
            False,
        )

    source_span_value = evidence_row.get("source_span")
    source_quote_value = (
        source_span_value.get("quote") if isinstance(source_span_value, Mapping) else None
    )
    if (
        not quote
        or not isinstance(source_quote_value, str)
        or normalize_text(quote) not in normalize_text(source_quote_value)
    ):
        return (
            {
                "item_id": item_id,
                "evidence_id": evidence_row["id"],
                "key": key,
                "value": stored_value,
                "confidence": confidence,
                "validation_result": "rejected",
                "reason": "quote_not_found",
            },
            False,
        )

    if not in_vocabulary:
        return (
            {
                "item_id": item_id,
                "evidence_id": evidence_row["id"],
                "key": key,
                "value": None,
                "confidence": confidence,
                "validation_result": "rejected",
                "reason": "value_out_of_vocabulary",
            },
            False,
        )

    item_model = item.get("model")
    matches_rule = _quote_matches(
        key,
        quote,
        value,
        model_text=item_model if isinstance(item_model, str) else "",
        base_date=base_date,
        confirmed_model_key=model_by_item.get(item_id),
        use_confirmed_model=True,
        confirmed_warranty=warranty_by_item.get(item_id),
        use_confirmed_warranty=True,
    )
    return (
        {
            "item_id": item_id,
            "evidence_id": evidence_row["id"],
            "key": key,
            "value": value,
            "confidence": confidence,
            "validation_result": "confirmed" if matches_rule else "proposed",
            "reason": None if matches_rule else "rule_not_confirmed",
        },
        False,
    )


def _entry_by_key(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    entries = payload.get("items", [])
    if not isinstance(entries, Sequence):
        return {}
    return {
        cast(str, entry["source_item_key"]): cast(Mapping[str, Any], entry)
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("source_item_key"), str)
    }


def _validate_and_store(
    conn: Connection,
    claim: EnrichJobClaim,
    context: _ObservationContext,
    response: EnrichmentResponse,
    settings: EnrichSettings,
    now: datetime,
) -> dict[str, Any]:
    if not grant_is_readable(
        conn,
        group_id=claim.group_id,
        grant_id=claim.grant_id,
        now=now,
    ):
        cancel_enrich(conn, claim, now)
        return {"state": "cancelled", "status": "cancelled", "tags_added": 0, "items_changed": 0}

    base_date = warranty_base_date(context.payload.get("posted_at"), context.observed_at)
    entry_by_key = _entry_by_key(context.payload)
    model_by_item, warranty_by_item = _confirmed_dependencies(conn, context.observation_id)
    selected = _dedupe_proposals(response.proposals)
    selected.sort(
        key=lambda item: (
            0 if item[1]["key"] == "model_key" else 1 if item[1]["key"] == "warranty" else 2,
            item[0],
        )
    )

    tags_to_store: list[dict[str, Any]] = []
    validation_counts = {result: 0 for result in _VALIDATION_RESULTS}
    unknown_reference = 0
    model_version = response.model_version or settings.ollama_model
    for _index, proposal in selected:
        tag_row, no_tag_unknown_reference = _tag_for_proposal(
            proposal,
            context,
            entry_by_key,
            base_date,
            model_by_item,
            warranty_by_item,
        )
        if no_tag_unknown_reference:
            unknown_reference += 1
            continue
        if tag_row is None:
            continue
        validation_result = cast(str, tag_row["validation_result"])
        validation_counts[validation_result] += 1
        tags_to_store.append(tag_row)
        item_id = cast(UUID, tag_row["item_id"])
        key = cast(str, tag_row["key"])
        value = cast(str | None, tag_row["value"])
        if validation_result == "confirmed" and value is not None:
            if key == "model_key":
                model_by_item.setdefault(item_id, value)
            elif key == "warranty":
                warranty_by_item.setdefault(item_id, value)

    touched_item_ids = sorted({cast(UUID, row["item_id"]) for row in tags_to_store}, key=str)
    if touched_item_ids:
        conn.execute(
            select(listing_items.c.id)
            .where(listing_items.c.id.in_(touched_item_ids))
            .order_by(listing_items.c.id)
            .with_for_update()
        ).all()

    tags_added = 0
    for row in tags_to_store:
        result = conn.execute(
            pg_insert(tags)
            .values(
                id=uuid4(),
                observation_id=context.observation_id,
                post_id=context.post_id,
                item_id=row["item_id"],
                evidence_id=row["evidence_id"],
                key=row["key"],
                value=row["value"],
                method="model",
                model_version=model_version,
                confidence=row["confidence"],
                rule_version=ENRICH_RULE_VERSION,
                validation_result=row["validation_result"],
                reason=row["reason"],
            )
            .on_conflict_do_nothing(constraint="uq_tags_observation_item_key_method_version")
            .returning(tags.c.id)
        )
        if result.scalar_one_or_none() is not None:
            tags_added += 1

    items_changed = sum(
        recompute_listing_item_projection(conn, item_id, already_locked=True)
        for item_id in touched_item_ids
    )
    job_result = {
        "status": "succeeded",
        "validation_results": validation_counts,
        "unknown_reference": unknown_reference,
        "tool_calls_ignored": response.tool_calls_ignored,
        "tags_added": tags_added,
        "items_changed": items_changed,
    }
    complete_enrich(conn, claim, now, job_result)
    return {"state": "succeeded", **job_result}


def _complete_without_tags(
    engine: Engine,
    claim: EnrichJobClaim,
    now: datetime,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    with engine.begin() as conn:
        if not grant_is_readable(
            conn,
            group_id=claim.group_id,
            grant_id=claim.grant_id,
            now=now,
        ):
            cancel_enrich(conn, claim, now)
            return {"state": "cancelled", "status": "cancelled"}
        complete_enrich(conn, claim, now, result)
    return {"state": "succeeded", **result}


def _job_snapshot(engine: Engine, job_id: UUID) -> dict[str, Any]:
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(jobs.c.state, jobs.c.retry_count, jobs.c.due_at, jobs.c.result).where(
                    jobs.c.id == job_id
                )
            )
            .mappings()
            .one()
        )
    return {
        "job_id": str(job_id),
        "state": row["state"],
        "retry_count": row["retry_count"],
        "due_at": cast(datetime, row["due_at"]).isoformat(),
        "result": row["result"],
    }


def run_enrich_once(
    engine: Engine,
    *,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Lease and process at most one due enrichment job."""
    settings = get_enrich_settings()
    normalized_now = now or datetime.now(UTC)
    lease_minutes = max(ENRICH_LEASE_MINUTES, (settings.timeout_seconds + 59) // 60 + 1)
    with engine.connect() as conn:
        claim = claim_enrich_next(conn, normalized_now, lease_minutes=lease_minutes)
    if claim is None:
        return None

    context = _load_context(engine, claim, normalized_now)
    if context is None:
        return _job_snapshot(engine, claim.job_id)

    user_content = _build_user_content(context)
    try:
        response = call_ollama(settings, user_content, transport=transport)
    except InvalidModelOutput as error:
        invalid_result = {
            "status": "invalid_output",
            "validation_results": {result: 0 for result in _VALIDATION_RESULTS},
            "unknown_reference": 0,
            "tool_calls_ignored": error.tool_calls_ignored,
            "tags_added": 0,
            "items_changed": 0,
        }
        result = _complete_without_tags(engine, claim, normalized_now, invalid_result)
        return {"job_id": str(claim.job_id), **result}
    except EnrichRetryableError as error:
        with engine.begin() as conn:
            state = retry_enrich(conn, claim, normalized_now, str(error))
        return _job_snapshot(engine, claim.job_id) | {"state": state}
    except EnrichRequestError as error:
        with engine.begin() as conn:
            fail_enrich(conn, claim, normalized_now, str(error))
        return _job_snapshot(engine, claim.job_id)

    if response.model_version is None:
        response = EnrichmentResponse(
            proposals=response.proposals,
            model_version=settings.ollama_model,
            tool_calls_ignored=response.tool_calls_ignored,
        )
    with engine.begin() as conn:
        result = _validate_and_store(conn, claim, context, response, settings, normalized_now)
    return {"job_id": str(claim.job_id), **result}


def enrich_status(engine: Engine, *, now: datetime | None = None) -> dict[str, Any]:
    """Return queued/leased/completed counts and the oldest queued age."""
    normalized_now = now or datetime.now(UTC)
    with engine.connect() as conn:
        state_rows = (
            conn.execute(
                select(jobs.c.state, func.count().label("count"))
                .where(jobs.c.kind == "enrich")
                .group_by(jobs.c.state)
            )
            .mappings()
            .all()
        )
        oldest_due_at: datetime | None = cast(
            datetime | None,
            conn.execute(
                select(func.min(jobs.c.due_at)).where(
                    jobs.c.kind == "enrich", jobs.c.state == "queued"
                )
            ).scalar_one(),
        )
    counts = {"queued": 0, "leased": 0, "succeeded": 0, "failed": 0, "cancelled": 0}
    for row in state_rows:
        counts[cast(str, row["state"])] = cast(int, row["count"])
    oldest = oldest_due_at
    return {
        "states": counts,
        "oldest_queued_at": oldest.isoformat() if oldest is not None else None,
        "oldest_queued_age_seconds": (
            max(0, int((normalized_now - oldest).total_seconds())) if oldest is not None else None
        ),
    }


def seconds_until_next_enrich_job(engine: Engine, *, now: datetime | None = None) -> float | None:
    normalized_now = now or datetime.now(UTC)
    with engine.connect() as conn:
        due_at: datetime | None = cast(
            datetime | None,
            conn.execute(
                select(func.min(jobs.c.due_at)).where(
                    jobs.c.kind == "enrich", jobs.c.state == "queued"
                )
            ).scalar_one(),
        )
    due = due_at
    return max(0.0, (due - normalized_now).total_seconds()) if due is not None else None


def run_enrich_worker(
    engine: Engine,
    *,
    transport: httpx.BaseTransport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Keep polling the enrich queue until the process is interrupted."""
    while True:
        if run_enrich_once(engine, transport=transport) is not None:
            continue
        delay = seconds_until_next_enrich_job(engine)
        sleeper(5.0 if delay is None else max(0.1, min(delay, 60.0)))
