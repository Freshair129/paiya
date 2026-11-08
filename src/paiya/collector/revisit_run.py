"""Execute one synthetic permalink revisit using the A2 ingest path."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import Engine, func, or_, select, update

from paiya.collector.adapter import ADAPTER_VERSION
from paiya.collector.errors import AdapterError
from paiya.collector.revisit_script import RevisitScriptAdapter
from paiya.ingest.service import ingest_fixture
from paiya.logging import emit_event, safe_token
from paiya.scheduler.errors import QueueError
from paiya.scheduler.models import JobClaim
from paiya.scheduler.queue import cancel_claim, complete, fail
from paiya.store.fixture_scope import (
    FixtureScope,
    verify_fixture_scope,
    verify_fixture_scope_for_commit,
)
from paiya.store.tables import (
    capture_runs,
    current_items,
    listing_items,
    observations,
    posts,
    sale_events,
    watches,
)

ACCESS_ERRORS = {
    "unavailable": "post_unavailable",
    "permission_denied": "permission_denied",
    "session_required": "session_required",
    "challenge_required": "challenge_required",
}


@dataclass(frozen=True)
class RevisitResult:
    run_id: UUID
    post_id: UUID
    coverage: str
    error_code: str | None
    observation_id: UUID


def execute_revisit(
    engine: Engine,
    scope: FixtureScope,
    claim: JobClaim,
    adapter: RevisitScriptAdapter,
    now: datetime,
    *,
    staged_assets: Mapping[str, bytes] | None = None,
    media_root: Path | None = None,
) -> RevisitResult:
    """Read the claimed post slot, ingest its envelope, and finish the run summary."""
    end_time = _utc(now)
    if claim.kind != "revisit":
        raise ValueError("A4 collector only executes revisit jobs")
    if claim.group_id != scope.group_id or claim.grant_id != scope.grant_id:
        raise PermissionError("The claimed job does not match the trusted fixture scope")
    log_started_at = datetime.now(UTC)
    log_started_clock = perf_counter()
    try:
        post_id = UUID(claim.target_key)
    except ValueError as error:
        _record_failure(engine, claim, end_time, "adapter_error")
        _log_capture_finished(
            claim,
            started_at=log_started_at,
            started_clock=log_started_clock,
            result="none",
            reason="adapter_error",
            cards_seen=0,
            cards_parsed=0,
        )
        raise AdapterError("adapter_error", "Revisit target_key must be a post UUID") from error

    with engine.connect() as conn:
        post = (
            conn.execute(
                select(posts.c.source_post_key, posts.c.permalink).where(
                    posts.c.id == post_id,
                    posts.c.group_id == scope.group_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        run = (
            conn.execute(
                select(capture_runs.c.id, capture_runs.c.group_id, capture_runs.c.grant_id).where(
                    capture_runs.c.id == claim.run_id
                )
            )
            .mappings()
            .one_or_none()
        )
    if post is None:
        _record_failure(engine, claim, end_time, "adapter_error")
        _log_capture_finished(
            claim,
            started_at=log_started_at,
            started_clock=log_started_clock,
            result="none",
            reason="adapter_error",
            cards_seen=0,
            cards_parsed=0,
        )
        raise AdapterError(
            "adapter_error", "Revisit target post was not found in the claimed group"
        )
    if run is None or run["group_id"] != scope.group_id or run["grant_id"] != scope.grant_id:
        raise PermissionError("The claimed capture run does not match the fixture scope")

    try:
        visit = adapter.read_visit(scope.group_key, post["source_post_key"], claim.due_at)
        envelope = _envelope(visit, claim, scope.group_key, post_id, post["permalink"])
        observation_id = ingest_fixture(
            engine,
            scope,
            envelope,
            staged_assets or {},
            media_root or Path("."),
        )
    except PermissionError:
        if _grant_currently_available(engine, scope):
            _record_failure(engine, claim, end_time, "adapter_error")
            reason = "adapter_error"
        else:
            _record_cancellation(engine, claim, end_time)
            reason = "grant_unavailable"
        _log_capture_finished(
            claim,
            started_at=log_started_at,
            started_clock=log_started_clock,
            result="none",
            reason=reason,
            cards_seen=1,
            cards_parsed=0,
        )
        raise
    except AdapterError as error:
        _record_failure(engine, claim, end_time, error.code)
        _log_capture_finished(
            claim,
            started_at=log_started_at,
            started_clock=log_started_clock,
            result="none",
            reason=error.code,
            cards_seen=0,
            cards_parsed=0,
        )
        raise
    except Exception:
        _record_failure(engine, claim, end_time, "adapter_error")
        _log_capture_finished(
            claim,
            started_at=log_started_at,
            started_clock=log_started_clock,
            result="none",
            reason="adapter_error",
            cards_seen=0,
            cards_parsed=0,
        )
        raise

    error_code = ACCESS_ERRORS.get(visit.access)
    coverage = "complete" if visit.access == "ok" else "none"
    try:
        with engine.begin() as conn:
            verify_fixture_scope_for_commit(conn, scope, "capture")
            conn.execute(
                update(capture_runs)
                .where(capture_runs.c.id == claim.run_id)
                .values(
                    ended_at=end_time,
                    session_result="ok",
                    sort_result="not_applicable",
                    coverage=coverage,
                    partial_reasons=[],
                    stop_reason=None if visit.access == "ok" else "access_blocked",
                    cards_seen=1,
                    cards_parsed=1 if visit.access == "ok" else 0,
                    scroll_steps=0,
                    error_code=error_code,
                )
            )
            if visit.access == "ok":
                _advance_slow_watches(conn, post_id, end_time)
            complete(conn, claim.job_id, claim.lease_token, end_time)
    except PermissionError:
        _record_cancellation(engine, claim, end_time)
        _log_capture_finished(
            claim,
            started_at=log_started_at,
            started_clock=log_started_clock,
            result=coverage,
            reason="grant_unavailable",
            cards_seen=1,
            cards_parsed=1 if visit.access == "ok" else 0,
        )
        raise

    _log_capture_finished(
        claim,
        started_at=log_started_at,
        started_clock=log_started_clock,
        result=coverage,
        reason=error_code or "completed",
        cards_seen=1,
        cards_parsed=1 if visit.access == "ok" else 0,
    )
    return RevisitResult(
        run_id=claim.run_id,
        post_id=post_id,
        coverage=coverage,
        error_code=error_code,
        observation_id=observation_id,
    )


def _log_capture_finished(
    claim: JobClaim,
    *,
    started_at: datetime,
    started_clock: float,
    result: str,
    reason: str,
    cards_seen: int,
    cards_parsed: int,
) -> None:
    queue_wait_ms = max(0, int((started_at - _utc(claim.due_at)).total_seconds() * 1000))
    emit_event(
        "capture_run_finished",
        run_id=str(claim.run_id),
        group_id=str(claim.group_id),
        job_kind=claim.kind,
        adapter_version=ADAPTER_VERSION,
        result=result,
        reason=safe_token(reason, "unknown"),
        duration_ms=max(0, int((perf_counter() - started_clock) * 1000)),
        queue_wait_ms=queue_wait_ms,
        cards_seen=cards_seen,
        cards_parsed=cards_parsed,
    )


def _envelope(
    visit: Any,
    claim: JobClaim,
    group_key: str,
    post_id: UUID,
    permalink: str,
) -> dict[str, Any]:
    if visit.envelope is None:
        envelope: dict[str, Any] = {
            "run_id": str(claim.run_id),
            "capture_id": str(uuid5(claim.run_id, str(post_id))),
            "grant_id": None,
            "source_kind": "fixture",
            "job_kind": "revisit",
            "page_check": {"session": "ok", "sort": "not_applicable"},
            "assets": {},
            "payload": {
                "schema_version": 1,
                "group_key": group_key,
                "source_post_key": visit.source_post_key,
                "permalink": permalink,
                "observed_at": _iso(visit.at),
                "access": visit.access,
            },
        }
        return envelope

    envelope = deepcopy(visit.envelope)
    envelope["run_id"] = str(claim.run_id)
    envelope["capture_id"] = str(uuid5(claim.run_id, str(post_id)))
    envelope["grant_id"] = None
    envelope["source_kind"] = "fixture"
    envelope["job_kind"] = "revisit"
    envelope["page_check"] = {"session": "ok", "sort": "not_applicable"}
    envelope["assets"] = envelope.get("assets", {})
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise AdapterError("adapter_error", "Revisit envelope payload must be an object")
    payload["observed_at"] = _iso(visit.at)
    return envelope


def _advance_slow_watches(conn: Any, post_id: UUID, now: datetime) -> None:
    first_seen = conn.execute(
        select(func.min(observations.c.observed_at)).where(observations.c.post_id == post_id)
    ).scalar_one()
    if first_seen is None or first_seen + timedelta(hours=24) > now:
        return
    open_item = conn.execute(
        select(listing_items.c.id)
        .select_from(
            listing_items.outerjoin(
                current_items, current_items.c.item_id == listing_items.c.id
            ).outerjoin(sale_events, sale_events.c.id == current_items.c.status_event_id)
        )
        .where(
            listing_items.c.post_id == post_id,
            or_(
                sale_events.c.sale_status.is_(None),
                sale_events.c.sale_status.not_in(("sold", "withdrawn")),
            ),
        )
        .limit(1)
    ).scalar_one_or_none()
    if open_item is None:
        return
    conn.execute(
        update(watches)
        .where(
            watches.c.post_id == post_id,
            watches.c.item_id.is_(None),
            watches.c.next_due_at <= now,
            or_(watches.c.hot_until.is_(None), watches.c.hot_until <= now),
        )
        .values(next_due_at=now + timedelta(hours=24))
    )


def _record_failure(engine: Engine, claim: JobClaim, now: datetime, code: str) -> None:
    with engine.connect() as conn:
        try:
            fail(conn, claim.job_id, claim.lease_token, now, code)
        except QueueError as error:
            if error.code == "lease_lost":
                return
            raise


def _record_cancellation(engine: Engine, claim: JobClaim, now: datetime) -> None:
    with engine.connect() as conn:
        try:
            cancel_claim(conn, claim.job_id, claim.lease_token, now, "grant_unavailable")
        except QueueError as error:
            if error.code == "lease_lost":
                return
            raise


def _grant_currently_available(engine: Engine, scope: FixtureScope) -> bool:
    with engine.connect() as conn:
        try:
            verify_fixture_scope(conn, scope, "capture")
        except PermissionError:
            return False
        return True


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")
