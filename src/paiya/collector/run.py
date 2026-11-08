"""Execute one claimed discovery job against a bounded adapter result."""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import Engine, select, update

from paiya.collector.adapter import ADAPTER_VERSION, CollectorAdapter, FeedCard, FeedRunData
from paiya.collector.errors import AdapterError
from paiya.ingest.service import ingest_fixture
from paiya.logging import emit_event, safe_token
from paiya.scheduler.errors import QueueError
from paiya.scheduler.models import JobClaim
from paiya.scheduler.queue import cancel_claim, complete, fail
from paiya.scheduler.settings import SchedulerSettings
from paiya.store.fixture_scope import (
    FixtureScope,
    verify_fixture_scope,
    verify_fixture_scope_for_commit,
)
from paiya.store.tables import capture_runs, groups, posts


@dataclass(frozen=True)
class RunResult:
    run_id: UUID
    coverage: str
    partial_reasons: tuple[str, ...]
    stop_reason: str
    cards_seen: int
    cards_parsed: int
    scroll_steps: int
    error_code: str | None
    checkpoint_advanced: bool
    ingested_keys: tuple[str, ...]


def execute_discovery(
    engine: Engine,
    scope: FixtureScope,
    claim: JobClaim,
    adapter: CollectorAdapter,
    run_label: str,
    now: datetime,
    settings: SchedulerSettings = SchedulerSettings(),
    *,
    staged_assets: Mapping[str, bytes] | None = None,
    media_root: Path | None = None,
    monotonic_clock: Callable[[], float] = monotonic,
) -> RunResult:
    """Read a synthetic feed, ingest its visible cards, and complete the claim."""
    end_time = _utc(now)
    if claim.kind != "discover":
        raise ValueError("A3 collector only executes discover jobs")
    if claim.group_id != scope.group_id or claim.grant_id != scope.grant_id:
        raise PermissionError("The claimed job does not match the trusted fixture scope")

    log_started_at = datetime.now(UTC)
    log_started_clock = perf_counter()
    try:
        feed_run = adapter.read_run(scope.group_key, run_label, settings.cards_per_step)
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

    started_at = _utc(feed_run.started_at)
    with engine.connect() as conn:
        existing_run = (
            conn.execute(
                select(capture_runs.c.id, capture_runs.c.group_id, capture_runs.c.grant_id).where(
                    capture_runs.c.id == claim.run_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            existing_run is None
            or existing_run["group_id"] != scope.group_id
            or existing_run["grant_id"] != scope.grant_id
        ):
            raise PermissionError("The claimed capture run does not match the fixture scope")
        preexisting_keys: set[str] = set(
            conn.execute(
                select(posts.c.source_post_key).where(posts.c.group_id == scope.group_id)
            ).scalars()
        )

    stop_reason = "feed_end"
    cards_seen = 0
    cards_parsed = 0
    scroll_steps = 0
    parse_failures = False
    consecutive_overlap = 0
    top_keys: list[str] = []
    ingested_keys: list[str] = []
    started_clock = monotonic_clock()
    blocked_error = _blocked_error(feed_run)

    if blocked_error is not None:
        stop_reason = "access_blocked"
    else:
        for batch_index, batch in enumerate(feed_run.batches):
            if monotonic_clock() - started_clock >= settings.time_budget_seconds:
                stop_reason = "time_budget_exhausted"
                break
            if batch_index > settings.scroll_budget_steps:
                stop_reason = "scroll_budget_exhausted"
                break
            if batch_index > 0:
                scroll_steps += 1
            overlap_reached_in_batch = False
            for card in batch:
                if monotonic_clock() - started_clock >= settings.time_budget_seconds:
                    stop_reason = "time_budget_exhausted"
                    break
                cards_seen += 1
                if card.parse != "ok" or card.source_post_key is None or card.envelope is None:
                    parse_failures = True
                    if not card.pinned and not card.suggested and not overlap_reached_in_batch:
                        consecutive_overlap = 0
                    continue

                cards_parsed += 1
                key = card.source_post_key
                if (
                    not card.pinned
                    and not card.suggested
                    and key not in top_keys
                    and len(top_keys) < 20
                ):
                    top_keys.append(key)
                try:
                    envelope = _run_envelope(card, feed_run, claim.run_id, scope.group_key)
                    ingest_fixture(
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
                        cards_seen=cards_seen,
                        cards_parsed=cards_parsed,
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
                        cards_seen=cards_seen,
                        cards_parsed=cards_parsed,
                    )
                    raise
                ingested_keys.append(key)

                if overlap_reached_in_batch or card.pinned or card.suggested:
                    continue
                if key in preexisting_keys:
                    consecutive_overlap += 1
                    if consecutive_overlap >= settings.overlap_count:
                        overlap_reached_in_batch = True
                else:
                    consecutive_overlap = 0
            if overlap_reached_in_batch:
                stop_reason = "overlap_reached"
                break
            if stop_reason == "time_budget_exhausted":
                break

        else:
            stop_reason = "feed_end"

    if blocked_error is not None:
        error_code = blocked_error
    elif cards_parsed == 0:
        error_code = "adapter_error"
    else:
        error_code = None

    if stop_reason == "access_blocked" or cards_parsed == 0:
        coverage = "none"
        partial_reasons: list[str] = []
    else:
        partial_reasons = []
        if feed_run.sort == "unverified":
            partial_reasons.append("sort_unverified")
        if stop_reason in {"time_budget_exhausted", "scroll_budget_exhausted"}:
            partial_reasons.append("budget_before_overlap")
        if stop_reason == "feed_end" and preexisting_keys:
            partial_reasons.append("feed_stopped_before_overlap")
        if parse_failures:
            partial_reasons.append("parse_failures")
        coverage = "partial" if partial_reasons else "complete"

    checkpoint_advanced = coverage == "complete"
    checkpoint = {
        "run_id": str(claim.run_id),
        "top_keys": top_keys,
        "advanced_at": end_time.isoformat().replace("+00:00", "Z"),
    }
    try:
        with engine.begin() as conn:
            verify_fixture_scope_for_commit(conn, scope, "capture")
            conn.execute(
                update(capture_runs)
                .where(capture_runs.c.id == claim.run_id)
                .values(
                    started_at=started_at,
                    ended_at=end_time,
                    session_result=feed_run.session,
                    sort_result=feed_run.sort,
                    coverage=coverage,
                    partial_reasons=partial_reasons,
                    stop_reason=stop_reason,
                    cards_seen=cards_seen,
                    cards_parsed=cards_parsed,
                    scroll_steps=scroll_steps,
                    error_code=error_code,
                )
            )
            if checkpoint_advanced:
                conn.execute(
                    update(groups)
                    .where(groups.c.id == scope.group_id)
                    .values(discovery_checkpoint=checkpoint)
                )
            complete(conn, claim.job_id, claim.lease_token, end_time)
    except PermissionError:
        _record_cancellation(engine, claim, end_time)
        _log_capture_finished(
            claim,
            started_at=log_started_at,
            started_clock=log_started_clock,
            result=coverage,
            reason="grant_unavailable",
            cards_seen=cards_seen,
            cards_parsed=cards_parsed,
        )
        raise

    _log_capture_finished(
        claim,
        started_at=log_started_at,
        started_clock=log_started_clock,
        result=coverage,
        reason=error_code or stop_reason,
        cards_seen=cards_seen,
        cards_parsed=cards_parsed,
    )
    return RunResult(
        run_id=claim.run_id,
        coverage=coverage,
        partial_reasons=tuple(partial_reasons),
        stop_reason=stop_reason,
        cards_seen=cards_seen,
        cards_parsed=cards_parsed,
        scroll_steps=scroll_steps,
        error_code=error_code,
        checkpoint_advanced=checkpoint_advanced,
        ingested_keys=tuple(ingested_keys),
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


def _blocked_error(run: FeedRunData) -> str | None:
    if run.access == "challenge_required":
        return "challenge_required"
    if run.access == "permission_denied":
        return "permission_denied"
    if run.session == "required" or run.access == "session_required":
        return "session_required"
    if run.access == "unavailable":
        return "adapter_error"
    if run.access != "ok":
        return "adapter_error"
    return None


def _run_envelope(
    card: FeedCard, feed_run: FeedRunData, run_id: UUID, group_key: str
) -> dict[str, Any]:
    if card.source_post_key is None or card.envelope is None:
        raise ValueError("A parsed card must have a stable key and envelope")
    envelope = deepcopy(card.envelope)
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise AdapterError("adapter_error", "Envelope payload must be an object")
    if payload.get("group_key") != group_key:
        raise AdapterError("adapter_error", "Envelope group_key does not match the fixture grant")
    if payload.get("source_post_key") != card.source_post_key or payload.get("access") != "ok":
        raise AdapterError("adapter_error", "Envelope does not match its readable feed card")
    envelope["run_id"] = str(run_id)
    envelope["capture_id"] = str(uuid5(run_id, card.source_post_key))
    envelope["source_kind"] = "fixture"
    envelope["job_kind"] = "discover"
    envelope["grant_id"] = None
    envelope["page_check"] = {"session": feed_run.session, "sort": feed_run.sort}
    payload["observed_at"] = feed_run.started_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return envelope


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
