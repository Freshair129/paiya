"""Local-only manual-share import service for M1a.

Permalinks are parsed as text. This package deliberately has no HTTP client and
does not open source URLs.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import Connection, Engine, func, insert, select, update

from paiya.grants.errors import GrantError
from paiya.ingest.validation import validate_observation
from paiya.store.tables import (
    capture_runs,
    evidence,
    groups,
    jobs,
    listing_items,
    observations,
    posts,
    price_facts,
    source_grants,
)

MANUAL_PARSER_VERSION = "manual-share-v1.0"
_MODEL = re.compile(
    r"(?<![\w])(?P<model>(?:(?:RTX|GTX|RX)\s*)?\d{4}(?:\s*(?:TI|SUPER))?(?:\s*\d{1,2}\s*GB)?)(?!\w)",
    re.IGNORECASE,
)
_AMOUNT_TEXT = r"(?P<amount>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<cents>\d{1,2}))?"
_AMOUNT = re.compile(_AMOUNT_TEXT)
_RANGE_AFTER_AMOUNT = re.compile(rf"\s*(?:[-–—]|to)\s*{_AMOUNT_TEXT}", re.IGNORECASE)
_CLASSIFIED = re.compile(
    rf"(?P<kind>ค่าส่ง|ค่าขนส่ง|shipping|ส่ง|มัดจำ|deposit|ผ่อน|installment|รับซื้อ|offer)"
    rf"\s*(?:รวม|ประมาณ|จำนวน)?\s*(?:[:=]\s*)?{_AMOUNT_TEXT}",
    re.IGNORECASE,
)
_URL_PATH = re.compile(r"^/groups/(?P<group>[A-Za-z0-9._-]+)/posts/(?P<post>\d+)/?$", re.ASCII)
_URL_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}


class ManualImportError(ValueError):
    """A local manual-import request was rejected without persistence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ManualImportConflict(ManualImportError):
    """An idempotency key was reused for a different manual-import body."""

    def __init__(self) -> None:
        super().__init__("idempotency_conflict", "Idempotency key was used with different content")


class _GrantScope:
    def __init__(
        self,
        grant_id: UUID,
        group_id: UUID,
        group_key: str,
        scopes: set[str],
        retention_days: int,
    ) -> None:
        self.grant_id = grant_id
        self.group_id = group_id
        self.group_key = group_key
        self.scopes = scopes
        self.retention_days = retention_days


class ManualImportService:
    """Preview and commit pasted text through the local database only."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def check_grant(self, grant: UUID | str) -> None:
        """Check the source grant before the caller reads a text file."""
        grant_id = _grant_id(grant)
        with self.engine.connect() as conn:
            _load_grant(conn, grant_id)

    def preview(self, text: str, permalink: str, grant: UUID | str) -> dict[str, Any]:
        """Parse a direct permalink and pasted text without writing any rows."""
        grant_id = _grant_id(grant)
        with self.engine.connect() as conn:
            scope = _load_grant(conn, grant_id)
        return _make_preview(text, permalink, scope)

    def commit(
        self,
        preview_input: dict[str, Any],
        idempotency_key: str,
        grant: UUID | str,
    ) -> dict[str, Any]:
        """Persist one observation, rechecking and locking its grant in-transaction."""
        grant_id = _grant_id(grant)
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise ManualImportError(
                "invalid_idempotency_key", "Idempotency key must be 1-200 characters"
            )
        logical_key = f"manual:{key}"

        with self.engine.begin() as conn:
            scope = _load_grant(conn, grant_id, lock=True)
            body_text = preview_input.get("post_text")
            permalink = preview_input.get("permalink")
            if not isinstance(body_text, str) or not isinstance(permalink, str):
                raise ManualImportError("invalid_preview", "Preview input is incomplete")
            rebuilt = _make_preview(body_text, permalink, scope)
            if (
                preview_input.get("grant_id") != str(grant_id)
                or preview_input.get("preview_digest") != rebuilt["preview_digest"]
            ):
                raise ManualImportError(
                    "preview_changed", "Preview input no longer matches its digest"
                )

            conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(logical_key, 0))))
            request_digest = cast(str, rebuilt["preview_digest"])
            existing = (
                conn.execute(
                    select(jobs.c.grant_id, jobs.c.group_id, jobs.c.request_digest, jobs.c.result)
                    .where(jobs.c.logical_key == logical_key)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if (
                    existing["grant_id"] != grant_id
                    or existing["group_id"] != scope.group_id
                    or existing["request_digest"] != request_digest
                ):
                    raise ManualImportConflict()
                result = existing["result"]
                if not isinstance(result, dict):
                    raise ManualImportError(
                        "idempotency_incomplete", "Saved import result is unavailable"
                    )
                return cast(dict[str, Any], result)

            result = _persist_observation(conn, scope, rebuilt, key, logical_key, request_digest)
            return result


def _grant_id(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (TypeError, ValueError) as error:
        raise GrantError("grant_unavailable", "No active manual-share grant was found") from error


def _load_grant(conn: Connection, grant_id: UUID, *, lock: bool = False) -> _GrantScope:
    query = (
        select(
            source_grants.c.id,
            source_grants.c.group_id,
            source_grants.c.source_kind,
            source_grants.c.scopes,
            source_grants.c.valid_from,
            source_grants.c.valid_until,
            source_grants.c.revoked_at,
            source_grants.c.retention_days,
            groups.c.group_key,
        )
        .select_from(source_grants.join(groups, groups.c.id == source_grants.c.group_id))
        .where(source_grants.c.id == grant_id)
    )
    if lock:
        query = query.with_for_update(read=True, of=source_grants)
    row = conn.execute(query).mappings().one_or_none()
    now = datetime.now(UTC)
    if (
        row is None
        or row["source_kind"] != "manual_share"
        or row["revoked_at"] is not None
        or row["valid_from"] > now
        or (row["valid_until"] is not None and row["valid_until"] <= now)
        or "capture" not in cast(list[str], row["scopes"])
    ):
        raise GrantError(
            "grant_unavailable", "No active manual-share grant with capture scope was found"
        )
    retention_days = cast(int, row["retention_days"])
    if retention_days <= 0:
        raise GrantError("grant_unavailable", "Manual-share grant has no retention period")
    return _GrantScope(
        grant_id=cast(UUID, row["id"]),
        group_id=cast(UUID, row["group_id"]),
        group_key=cast(str, row["group_key"]),
        scopes=set(cast(list[str], row["scopes"])),
        retention_days=retention_days,
    )


def _parse_permalink(permalink: str) -> tuple[str, str, str]:
    try:
        parsed = urlsplit(permalink.strip())
        port = parsed.port
    except ValueError as error:
        raise ManualImportError(
            "invalid_permalink", "Use a direct Facebook group post permalink"
        ) from error
    host = (parsed.hostname or "").casefold()
    match = _URL_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme.casefold() != "https"
        or host not in _URL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port != 443)
        or match is None
    ):
        raise ManualImportError("invalid_permalink", "Use a direct Facebook group post permalink")
    group_key = match.group("group")
    source_post_key = match.group("post")
    canonical_host = "www.facebook.com" if host == "facebook.com" else host
    canonical = f"https://{canonical_host}{parsed.path}"
    return group_key, source_post_key, canonical


def _make_preview(text: str, permalink: str, scope: _GrantScope) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ManualImportError("empty_text", "Pasted post text is required")
    group_key, source_post_key, canonical_permalink = _parse_permalink(permalink)
    if group_key != scope.group_key:
        raise ManualImportError(
            "grant_scope_mismatch", "Permalink does not match the selected grant"
        )
    items, unassigned_facts, evidence_entries, unresolved_item_keys = _parse_text(text)
    body: dict[str, Any] = {
        "source_kind": "manual_share",
        "grant_id": str(scope.grant_id),
        "group_id": str(scope.group_id),
        "group_key": scope.group_key,
        "source_post_key": source_post_key,
        "permalink": canonical_permalink,
        "post_text": text,
        "items": items,
        "unresolved_item_keys": unresolved_item_keys,
        "unassigned_price_facts": unassigned_facts,
        "evidence": evidence_entries,
        "parser_version": MANUAL_PARSER_VERSION,
    }
    digest = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    body["preview_digest"] = digest
    return body


def _parse_text(
    text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    model_matches = [
        match
        for match in _MODEL.finditer(text)
        if re.search(r"(?:RTX|GTX|RX)|\d\s*GB|\bTI\b|\bSUPER\b", match.group("model"), re.I)
    ]
    items: list[dict[str, Any]] = []
    unassigned: list[dict[str, Any]] = []
    evidence_entries: list[dict[str, Any]] = []
    item_ranges: list[tuple[int, int, dict[str, Any]]] = []
    used_keys: set[str] = set()
    unresolved_item_keys: list[str] = []
    evidence_number = 1

    classified = list(_CLASSIFIED.finditer(text))
    classified_amount_spans = [
        (
            match.start("amount"),
            match.end("cents") if match.group("cents") is not None else match.end("amount"),
        )
        for match in classified
    ]

    match_segments = [
        (
            match,
            model_matches[index + 1].start("model")
            if index + 1 < len(model_matches)
            else len(text),
        )
        for index, match in enumerate(model_matches)
    ]
    base_keys = [
        "gpu-" + re.sub(r"[^a-z0-9]+", "-", match.group("model").casefold()).strip("-")
        for match, _ in match_segments
    ]
    descriptor_by_index = [
        _item_identity_descriptor(text, match.end("model"), segment_end)
        for match, segment_end in match_segments
    ]
    base_counts = {key: base_keys.count(key) for key in set(base_keys)}
    descriptor_counts = {
        (key, descriptor): sum(
            1
            for candidate_key, candidate_descriptor in zip(
                base_keys, descriptor_by_index, strict=True
            )
            if candidate_key == key and candidate_descriptor == descriptor
        )
        for key, descriptor in zip(base_keys, descriptor_by_index, strict=True)
    }
    unresolved_occurrences: dict[str, int] = {}

    for index, (match, segment_end) in enumerate(match_segments):
        start = match.start("model")
        model = re.sub(r"\s+", " ", match.group("model")).strip()
        base_key = base_keys[index]
        descriptor = descriptor_by_index[index]
        if base_counts[base_key] == 1:
            source_item_key = base_key
        elif descriptor and descriptor_counts[(base_key, descriptor)] == 1:
            discriminator = hashlib.sha256(descriptor.encode("utf-8")).hexdigest()[:12]
            source_item_key = f"{base_key}-{discriminator}"
        else:
            occurrence = unresolved_occurrences.get(base_key, 0) + 1
            unresolved_occurrences[base_key] = occurrence
            source_item_key = f"{base_key}-unresolved-{occurrence}"
            unresolved_item_keys.append(source_item_key)
        if source_item_key in used_keys:
            raise ManualImportError("ambiguous_item_key", "Could not assign stable item keys")
        used_keys.add(source_item_key)
        item_facts: list[dict[str, Any]] = []
        item_evidence_keys: list[str] = []
        segment_matches = [
            candidate
            for candidate in _AMOUNT.finditer(text, match.end("model"), segment_end)
            if not any(
                candidate.start() < classified_end and candidate.end() > classified_start
                for classified_start, classified_end in classified_amount_spans
            )
        ]
        if segment_matches:
            amount_match = segment_matches[0]
            range_match = _RANGE_AFTER_AMOUNT.match(text, amount_match.end(), segment_end)
            amount_end = range_match.end() if range_match is not None else amount_match.end()
            local_key = f"e{evidence_number}"
            evidence_number += 1
            quote_end = _currency_end(text, amount_end)
            evidence_entries.append(_evidence(local_key, text, start, quote_end))
            item_evidence_keys.append(local_key)
            asking_fact = {
                "kind": "ask",
                "unit": "each",
                "currency": "THB",
                "amount": _money(amount_match.group("amount"), amount_match.group("cents")),
                "evidence_key": local_key,
            }
            if range_match is not None:
                asking_fact["amount_max"] = _money(
                    range_match.group("amount"), range_match.group("cents")
                )
            item_facts.append(asking_fact)
        else:
            local_key = f"e{evidence_number}"
            evidence_number += 1
            quote_end = match.end("model")
            evidence_entries.append(_evidence(local_key, text, start, quote_end))
            item_evidence_keys.append(local_key)
        item = {
            "source_item_key": source_item_key,
            "model": model,
            "price_facts": item_facts,
            "evidence_keys": item_evidence_keys,
        }
        items.append(item)
        item_ranges.append((start, segment_end, item))

    for match in classified:
        raw_kind = match.group("kind").casefold()
        kind = _fact_kind(raw_kind)
        if kind is None:
            continue
        amount_end = _currency_end(text, match.end("amount"))
        local_key = f"e{evidence_number}"
        evidence_number += 1
        evidence_entries.append(_evidence(local_key, text, match.start(), amount_end))
        fact = {
            "kind": kind,
            "unit": "bundle" if "รวม" in text[match.start() : match.end()] else "unknown",
            "currency": "THB",
            "amount": _money(match.group("amount"), match.group("cents")),
            "evidence_key": local_key,
        }
        if kind == "shipping":
            unassigned.append(fact)
            continue
        owner = next(
            (item for start, end, item in item_ranges if start <= match.start() < end), None
        )
        if owner is None:
            unassigned.append(fact)
        else:
            owner["price_facts"].append(fact)
            owner["evidence_keys"].append(local_key)

    return items, unassigned, evidence_entries, unresolved_item_keys


def _item_identity_descriptor(text: str, start: int, end: int) -> str:
    """Use non-price line details to distinguish repeated model names stably."""
    descriptor = _CLASSIFIED.sub(" ", text[start:end])
    descriptor = _AMOUNT.sub(" ", descriptor)
    descriptor = re.sub(r"\b(?:บาท|THB)\b", " ", descriptor, flags=re.IGNORECASE)
    descriptor = re.sub(r"[^\wก-๙]+", " ", descriptor, flags=re.UNICODE).casefold()
    return " ".join(descriptor.split())


def _fact_kind(value: str) -> str | None:
    if value in {"ค่าส่ง", "ค่าขนส่ง", "shipping", "ส่ง"}:
        return "shipping"
    if value in {"มัดจำ", "deposit"}:
        return "deposit"
    if value in {"ผ่อน", "installment"}:
        return "installment"
    if value in {"รับซื้อ", "offer"}:
        return "offer"
    return None


def _money(amount: str, cents: str | None) -> str:
    decimal_value = Decimal(amount.replace(",", ""))
    if cents is not None:
        decimal_value += Decimal(f"0.{cents}")
    return f"{decimal_value:.2f}"


def _currency_end(text: str, end: int) -> int:
    suffix = re.match(r"\s*(?:บาท|THB)", text[end:], re.IGNORECASE)
    return end + suffix.end() if suffix is not None else end


def _evidence(local_key: str, text: str, start: int, end: int) -> dict[str, Any]:
    return {
        "local_key": local_key,
        "role": "unknown",
        "source_span": {"field": "post_text", "quote": text[start:end], "start": start, "end": end},
        "media_state": "not_requested",
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _persist_observation(
    conn: Connection,
    scope: _GrantScope,
    preview: dict[str, Any],
    idempotency_key: str,
    logical_key: str,
    request_digest: str,
) -> dict[str, Any]:
    observed_at = datetime.now(UTC)
    run_id = uuid4()
    capture_id = str(uuid5(NAMESPACE_URL, f"paiya:manual:capture:{idempotency_key}"))
    envelope = _make_envelope(preview, run_id, capture_id, observed_at)
    validate_observation(envelope)
    payload_hash = hashlib.sha256(_canonical_json(envelope).encode("utf-8")).hexdigest()

    post_id = conn.execute(
        select(posts.c.id)
        .where(
            posts.c.group_id == scope.group_id,
            posts.c.source_post_key == preview["source_post_key"],
        )
        .with_for_update()
    ).scalar_one_or_none()
    if post_id is None:
        post_id = uuid4()
        conn.execute(
            insert(posts).values(
                id=post_id,
                group_id=scope.group_id,
                source_post_key=preview["source_post_key"],
                permalink=preview["permalink"],
                seller_id=None,
                posted_at=None,
            )
        )
    else:
        conn.execute(
            update(posts).where(posts.c.id == post_id).values(permalink=preview["permalink"])
        )

    job_id = uuid4()
    conn.execute(
        insert(jobs).values(
            id=job_id,
            group_id=scope.group_id,
            grant_id=scope.grant_id,
            kind="manual_import",
            target_key=f"manual:{scope.group_id}:{preview['source_post_key']}",
            logical_key=logical_key,
            request_digest=request_digest,
            due_at=observed_at,
            state="succeeded",
            retry_count=0,
            result=None,
        )
    )
    conn.execute(
        insert(capture_runs).values(
            id=run_id,
            job_id=job_id,
            group_id=scope.group_id,
            grant_id=scope.grant_id,
            source_kind="manual_share",
            started_at=observed_at,
            ended_at=observed_at,
            session_result=None,
            sort_result=None,
            coverage="partial",
            partial_reasons=["user_provided_text"],
            stop_reason=None,
            cards_seen=len(preview["items"]),
            cards_parsed=len(preview["items"]),
            scroll_steps=0,
            error_code=None,
        )
    )
    observation_id = uuid4()
    payload = envelope["payload"]
    activity = payload["activity"]
    conn.execute(
        insert(observations).values(
            id=observation_id,
            run_id=run_id,
            post_id=post_id,
            group_id=scope.group_id,
            grant_id=scope.grant_id,
            capture_id=capture_id,
            observed_at=observed_at,
            source_time=None,
            source_time_precision="unknown",
            access="ok",
            coverage=activity["coverage"],
            parser_version=MANUAL_PARSER_VERSION,
            source_reported_comment_count=None,
            distinct_comments_seen=0,
            distinct_known_commenters_seen=0,
            payload_hash=payload_hash,
            normalized_json=envelope,
        )
    )

    item_ids: dict[str, UUID] = {}
    unresolved_item_keys = set(preview["unresolved_item_keys"])
    for item in payload["items"]:
        source_item_key = item["source_item_key"]
        item_id = conn.execute(
            select(listing_items.c.id).where(
                listing_items.c.post_id == post_id,
                listing_items.c.source_item_key == source_item_key,
            )
        ).scalar_one_or_none()
        if item_id is None:
            item_id = uuid4()
            first_fact = item["price_facts"][0] if item["price_facts"] else None
            conn.execute(
                insert(listing_items).values(
                    id=item_id,
                    post_id=post_id,
                    source_item_key=source_item_key,
                    model_key=None,
                    variant=None,
                    vram_gb=None,
                    condition=None,
                    warranty=None,
                    warranty_until=None,
                    unit=first_fact["unit"] if first_fact else "unknown",
                    resolution=(
                        "unresolved" if source_item_key in unresolved_item_keys else "keyed"
                    ),
                )
            )
        elif source_item_key in unresolved_item_keys:
            conn.execute(
                update(listing_items)
                .where(
                    listing_items.c.post_id == post_id,
                    listing_items.c.source_item_key == source_item_key,
                )
                .values(resolution="unresolved")
            )
        item_ids[source_item_key] = item_id

    evidence_ids: dict[str, UUID] = {}
    evidence_items: dict[str, set[str]] = {}
    for item in payload["items"]:
        for evidence_key in item["evidence_keys"]:
            evidence_items.setdefault(evidence_key, set()).add(item["source_item_key"])
    for entry in payload["evidence"]:
        local_key = entry["local_key"]
        referenced = evidence_items.get(local_key, set())
        item_id = item_ids[next(iter(referenced))] if len(referenced) == 1 else None
        evidence_id = uuid4()
        evidence_ids[local_key] = evidence_id
        conn.execute(
            insert(evidence).values(
                id=evidence_id,
                observation_id=observation_id,
                post_id=post_id,
                grant_id=scope.grant_id,
                item_id=item_id,
                comment_version_id=None,
                media_asset_id=None,
                local_key=local_key,
                role="unknown",
                source_span=entry["source_span"],
                media_state="not_requested",
                media_reason=None,
                captured_at=observed_at,
            )
        )

    for item in payload["items"]:
        for fact in item["price_facts"]:
            _insert_price_fact(
                conn,
                observation_id,
                post_id,
                item_ids[item["source_item_key"]],
                item["source_item_key"],
                fact,
                evidence_ids[fact["evidence_key"]],
            )
    for fact in payload["unassigned_price_facts"]:
        _insert_price_fact(
            conn,
            observation_id,
            post_id,
            None,
            "post",
            fact,
            evidence_ids[fact["evidence_key"]],
        )

    result: dict[str, Any] = {
        "post_id": str(post_id),
        "observation_id": str(observation_id),
        "item_ids": [str(item_ids[item["source_item_key"]]) for item in payload["items"]],
        "source_kind": "manual_share",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
    }
    conn.execute(update(jobs).where(jobs.c.id == job_id).values(result=result))
    return result


def _make_envelope(
    preview: dict[str, Any], run_id: UUID, capture_id: str, observed_at: datetime
) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "capture_id": capture_id,
        "grant_id": preview["grant_id"],
        "source_kind": "manual_share",
        "job_kind": "manual_import",
        "page_check": None,
        "assets": {},
        "payload": {
            "schema_version": 2,
            "group_key": preview["group_key"],
            "source_post_key": preview["source_post_key"],
            "permalink": preview["permalink"],
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
            "access": "ok",
            "post_text": preview["post_text"],
            "seller": None,
            "items": preview["items"],
            "unassigned_price_facts": preview["unassigned_price_facts"],
            "activity": {
                "source_reported_comment_count": None,
                "distinct_comments_seen": 0,
                "distinct_known_commenters_seen": 0,
                "coverage": "partial",
            },
            "evidence": preview["evidence"],
        },
    }


def _insert_price_fact(
    conn: Connection,
    observation_id: UUID,
    post_id: UUID,
    item_id: UUID | None,
    item_key: str,
    fact: dict[str, Any],
    evidence_id: UUID,
) -> None:
    conn.execute(
        insert(price_facts).values(
            id=uuid4(),
            observation_id=observation_id,
            post_id=post_id,
            item_id=item_id,
            evidence_id=evidence_id,
            fact_key=f"price:{item_key}:{fact['kind']}:{fact['unit']}:{fact['evidence_key']}",
            price_kind=fact["kind"],
            unit=fact["unit"],
            amount=Decimal(fact["amount"]),
            amount_max=Decimal(fact["amount_max"]) if "amount_max" in fact else None,
            currency=fact["currency"],
            rule_version=MANUAL_PARSER_VERSION,
            validation_result="confirmed",
            reason=None,
        )
    )
