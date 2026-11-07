"""PostgreSQL-backed discovery slot scheduling and lease ownership."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from paiya.scheduler.errors import QueueError
from paiya.scheduler.models import JobClaim
from paiya.scheduler.settings import SchedulerSettings
from paiya.store.tables import (
    capture_gaps,
    capture_runs,
    current_items,
    groups,
    jobs,
    listing_items,
    observations,
    posts,
    sale_events,
    source_grants,
    watches,
)
from paiya.store.transactions import atomic

_CLAIM_LOCK = 0x5041495941434C4D
BROWSER_JOB_KINDS = ("discover", "revisit")
ERROR_CODES = {
    "session_required",
    "challenge_required",
    "permission_denied",
    "grant_unavailable",
    "adapter_error",
    "post_unavailable",
}


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(UTC)


def _slot(now: datetime, poll_minutes: int) -> datetime:
    interval_seconds = poll_minutes * 60
    timestamp = int(now.timestamp())
    return datetime.fromtimestamp(timestamp - timestamp % interval_seconds, tz=UTC)


def _slot_key(slot: datetime) -> str:
    return slot.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _grant_for_group(conn: Connection, group_id: UUID, now: datetime) -> UUID | None:
    rows = (
        conn.execute(
            select(source_grants.c.id, source_grants.c.scopes)
            .select_from(source_grants.join(groups, groups.c.id == source_grants.c.group_id))
            .where(
                source_grants.c.group_id == group_id,
                groups.c.source == "fixture",
                source_grants.c.source_kind == "fixture",
                source_grants.c.revoked_at.is_(None),
                source_grants.c.valid_from <= now,
                (source_grants.c.valid_until.is_(None) | (source_grants.c.valid_until > now)),
            )
            .order_by(source_grants.c.valid_from.desc(), source_grants.c.id)
        )
        .mappings()
        .all()
    )
    for row in rows:
        if "capture" in row["scopes"]:
            return cast(UUID, row["id"])
    return None


def _insert_gap(
    conn: Connection,
    *,
    group_id: UUID,
    gap_from: datetime,
    gap_to: datetime,
    reason: str,
    detected_at: datetime,
    compensated_by_job_id: UUID | None,
) -> None:
    if gap_to <= gap_from:
        return
    exists = conn.execute(
        select(capture_gaps.c.id).where(
            capture_gaps.c.group_id == group_id,
            capture_gaps.c.gap_from == gap_from,
            capture_gaps.c.gap_to == gap_to,
            capture_gaps.c.reason == reason,
        )
    ).scalar_one_or_none()
    if exists is None:
        conn.execute(
            insert(capture_gaps).values(
                id=uuid4(),
                group_id=group_id,
                gap_from=gap_from,
                gap_to=gap_to,
                reason=reason,
                detected_at=detected_at,
                compensated_by_job_id=compensated_by_job_id,
            )
        )


def _schedule_group_due(conn: Connection, now: datetime, group_id: UUID) -> UUID | None:
    group = (
        conn.execute(
            select(groups.c.source, groups.c.group_key, groups.c.enabled, groups.c.poll_minutes)
            .where(groups.c.id == group_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if (
        group is None
        or group["source"] != "fixture"
        or not group["enabled"]
        or group["poll_minutes"] not in (30, 60)
    ):
        return None

    grant_id = _grant_for_group(conn, group_id, now)
    if grant_id is None:
        return None
    slot = _slot(now, cast(int, group["poll_minutes"]))
    logical_key = f"discover:{group_id}:{_slot_key(slot)}"
    latest = (
        conn.execute(select(jobs).where(jobs.c.logical_key == logical_key).with_for_update())
        .mappings()
        .one_or_none()
    )
    queued = (
        conn.execute(
            select(jobs.c.id, jobs.c.due_at, jobs.c.logical_key)
            .where(
                jobs.c.group_id == group_id,
                jobs.c.kind == "discover",
                jobs.c.state == "queued",
            )
            .order_by(jobs.c.due_at, jobs.c.id)
            .with_for_update()
        )
        .mappings()
        .all()
    )
    old_queued = [row for row in queued if _scheduled_slot(dict(row)) < slot]
    if old_queued:
        conn.execute(
            update(jobs)
            .where(jobs.c.id.in_([row["id"] for row in old_queued]))
            .values(state="cancelled")
        )

    job_id: UUID | None = cast(UUID, latest["id"]) if latest is not None else None
    if latest is not None and latest["state"] == "cancelled":
        conn.execute(
            update(jobs)
            .where(jobs.c.id == latest["id"], jobs.c.state == "cancelled")
            .values(state="queued", grant_id=grant_id, due_at=slot)
        )
    elif latest is None:
        candidate_id = uuid4()
        inserted = conn.execute(
            pg_insert(jobs)
            .values(
                id=candidate_id,
                group_id=group_id,
                grant_id=grant_id,
                kind="discover",
                target_key=cast(str, group["group_key"]),
                logical_key=logical_key,
                request_digest=None,
                due_at=slot,
                state="queued",
                retry_count=0,
            )
            .on_conflict_do_nothing(index_elements=[jobs.c.logical_key])
            .returning(jobs.c.id)
        ).scalar_one_or_none()
        job_id = cast(UUID | None, inserted)
        if job_id is None:
            job_id = cast(
                UUID,
                conn.execute(
                    select(jobs.c.id).where(jobs.c.logical_key == logical_key)
                ).scalar_one(),
            )

    if old_queued:
        gap_from = min(_scheduled_slot(dict(row)) for row in old_queued)
        _insert_gap(
            conn,
            group_id=group_id,
            gap_from=gap_from,
            gap_to=slot,
            reason="missed_slots",
            detected_at=now,
            compensated_by_job_id=job_id,
        )
    return job_id


def schedule_group_due(conn: Connection, now: datetime, group_id: UUID) -> UUID | None:
    """Ensure a group's latest due slot is queued, preserving caller clocks."""
    normalized_now = _utc(now)
    with atomic(conn):
        return _schedule_group_due(conn, normalized_now, group_id)


def _ceil_slot(value: datetime, poll_minutes: int) -> datetime:
    slot = _slot(value, poll_minutes)
    return slot if slot == value.astimezone(UTC) else slot + timedelta(minutes=poll_minutes)


def _open_post_ids(conn: Connection, group_id: UUID) -> set[UUID]:
    return set(
        conn.execute(
            select(listing_items.c.post_id)
            .select_from(
                listing_items.outerjoin(
                    current_items, current_items.c.item_id == listing_items.c.id
                ).outerjoin(sale_events, sale_events.c.id == current_items.c.status_event_id)
            )
            .where(
                listing_items.c.post_id.in_(select(posts.c.id).where(posts.c.group_id == group_id)),
                or_(
                    sale_events.c.sale_status.is_(None),
                    sale_events.c.sale_status.not_in(("sold", "withdrawn")),
                ),
            )
            .distinct()
        ).scalars()
    )


def _schedule_revisits_for_group(
    conn: Connection,
    now: datetime,
    group_id: UUID,
    settings: SchedulerSettings,
) -> list[UUID]:
    group = (
        conn.execute(
            select(groups.c.enabled, groups.c.poll_minutes)
            .where(groups.c.id == group_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if group is None or not group["enabled"] or group["poll_minutes"] not in (30, 60):
        return []
    interval = cast(int, group["poll_minutes"])
    group_slot = _slot(now, interval)
    group_slot_end = group_slot + timedelta(minutes=interval)
    discover_state = conn.execute(
        select(jobs.c.state).where(
            jobs.c.group_id == group_id,
            jobs.c.kind == "discover",
            jobs.c.logical_key == f"discover:{group_id}:{_slot_key(group_slot)}",
        )
    ).scalar_one_or_none()
    if discover_state in {"queued", "leased"}:
        return []
    grant_id = _grant_for_group(conn, group_id, now)
    if grant_id is None:
        return []

    post_rows = (
        conn.execute(
            select(
                posts.c.id,
                func.min(observations.c.observed_at).label("first_seen_at"),
            )
            .select_from(posts.join(observations, observations.c.post_id == posts.c.id))
            .where(
                posts.c.group_id == group_id,
                observations.c.group_id == group_id,
                observations.c.grant_id == grant_id,
            )
            .group_by(posts.c.id)
        )
        .mappings()
        .all()
    )
    if not post_rows:
        return []

    post_ids = [cast(UUID, row["id"]) for row in post_rows]
    watch_rows = (
        conn.execute(
            select(
                watches.c.post_id,
                watches.c.saved_at,
                watches.c.hot_until,
                watches.c.next_due_at,
            ).where(watches.c.post_id.in_(post_ids), watches.c.item_id.is_(None))
        )
        .mappings()
        .all()
    )
    watch_by_post = {cast(UUID, row["post_id"]): dict(row) for row in watch_rows}
    last_success_by_post = {
        cast(UUID, row["target_key"]): cast(datetime, row["last_success_at"])
        for row in conn.execute(
            select(jobs.c.target_key, func.max(capture_runs.c.ended_at).label("last_success_at"))
            .select_from(jobs.join(capture_runs, capture_runs.c.job_id == jobs.c.id))
            .where(
                jobs.c.group_id == group_id,
                jobs.c.kind == "revisit",
                jobs.c.state == "succeeded",
                capture_runs.c.coverage == "complete",
                capture_runs.c.error_code.is_(None),
            )
            .group_by(jobs.c.target_key)
        ).mappings()
        if row["last_success_at"] is not None
    }
    active_targets: set[str] = set(
        conn.execute(
            select(jobs.c.target_key).where(
                jobs.c.group_id == group_id,
                jobs.c.kind == "revisit",
                jobs.c.state.in_(("queued", "leased")),
            )
        ).scalars()
    )
    spent = conn.execute(
        select(func.count())
        .select_from(jobs)
        .where(
            jobs.c.group_id == group_id,
            jobs.c.kind == "revisit",
            jobs.c.due_at >= group_slot,
            jobs.c.due_at < group_slot_end,
            jobs.c.state != "cancelled",
        )
    ).scalar_one()
    capacity = max(0, settings.revisit_budget_per_group_slot - spent)
    if capacity == 0:
        return []

    open_posts = _open_post_ids(conn, group_id)
    candidates: list[tuple[datetime, UUID, datetime]] = []
    cadence = timedelta(minutes=interval)
    for post in post_rows:
        post_id = cast(UUID, post["id"])
        first_seen = cast(datetime, post["first_seen_at"]).astimezone(UTC)
        if post_id not in open_posts or str(post_id) in active_targets:
            continue
        last_success = last_success_by_post.get(post_id)
        reasons: list[tuple[datetime, datetime]] = []

        new_window_end = first_seen + timedelta(hours=24)
        if now < new_window_end:
            next_new = _ceil_slot(
                max(first_seen, last_success + cadence) if last_success is not None else first_seen,
                interval,
            )
            if next_new <= now:
                reasons.append((next_new, group_slot))

        watch = watch_by_post.get(post_id)
        if watch is not None:
            hot_until = cast(datetime | None, watch["hot_until"])
            if hot_until is not None and hot_until > now:
                hot_start = hot_until - timedelta(hours=24)
                hot_base = (
                    max(hot_start, last_success + timedelta(minutes=10))
                    if last_success is not None
                    else hot_start
                )
                next_hot = _ceil_slot(hot_base, 10)
                hot_slot = _slot(now, 10)
                if next_hot <= now and hot_slot < hot_until:
                    reasons.append((next_hot, hot_slot))

            slow_due = cast(datetime | None, watch["next_due_at"])
            if (
                slow_due is not None
                and slow_due <= now
                and now >= new_window_end
                and (hot_until is None or hot_until <= now)
            ):
                reasons.append((slow_due.astimezone(UTC), group_slot))

        if reasons:
            overdue_at, due_at = min(reasons, key=lambda reason: (reason[0], reason[1]))
            candidates.append((overdue_at, post_id, due_at))

    created: list[UUID] = []
    for _overdue_at, post_id, due_at in sorted(candidates, key=lambda item: (item[0], item[1])):
        if len(created) >= capacity:
            break
        logical_key = f"revisit:{post_id}:{_slot_key(due_at)}"
        existing = (
            conn.execute(
                select(jobs.c.id, jobs.c.state)
                .where(jobs.c.logical_key == logical_key)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if existing["state"] != "cancelled":
                continue
            existing_id = cast(UUID, existing["id"])
            conn.execute(
                update(jobs)
                .where(jobs.c.id == existing_id, jobs.c.state == "cancelled")
                .values(
                    grant_id=grant_id,
                    due_at=due_at,
                    state="queued",
                    retry_count=0,
                    lease_token=None,
                    lease_until=None,
                )
            )
            created.append(existing_id)
            continue
        job_id = uuid4()
        inserted = conn.execute(
            pg_insert(jobs)
            .values(
                id=job_id,
                group_id=group_id,
                grant_id=grant_id,
                kind="revisit",
                target_key=str(post_id),
                logical_key=logical_key,
                request_digest=None,
                due_at=due_at,
                state="queued",
                retry_count=0,
            )
            .on_conflict_do_nothing()
            .returning(jobs.c.id)
        ).scalar_one_or_none()
        if inserted is not None:
            created.append(cast(UUID, inserted))
    return created


def schedule_due(
    conn: Connection,
    now: datetime,
    settings: SchedulerSettings = SchedulerSettings(),
) -> list[UUID]:
    """Schedule discover first, then eligible revisits within each group's slot budget."""
    normalized_now = _utc(now)
    scheduled: list[UUID] = []
    with atomic(conn):
        ids: Sequence[UUID] = (
            conn.execute(
                select(groups.c.id)
                .where(
                    groups.c.enabled.is_(True),
                    groups.c.source == "fixture",
                    groups.c.poll_minutes.in_((30, 60)),
                )
                .order_by(groups.c.id)
            )
            .scalars()
            .all()
        )
        for group_id in ids:
            job_id = _schedule_group_due(conn, normalized_now, group_id)
            if job_id is not None:
                scheduled.append(job_id)
            scheduled.extend(_schedule_revisits_for_group(conn, normalized_now, group_id, settings))
    return scheduled


def _ensure_run(
    conn: Connection, job: dict[str, Any], now: datetime, *, denied: bool = False
) -> UUID:
    existing = conn.execute(
        select(capture_runs.c.id).where(capture_runs.c.job_id == job["id"])
    ).scalar_one_or_none()
    if existing is not None:
        run_id = cast(UUID, existing)
        if denied:
            conn.execute(
                update(capture_runs)
                .where(capture_runs.c.id == run_id)
                .values(
                    ended_at=now,
                    coverage="none",
                    partial_reasons=[],
                    stop_reason="access_blocked",
                    error_code="grant_unavailable",
                )
            )
        return run_id

    run_id = uuid4()
    values: dict[str, Any] = {
        "id": run_id,
        "job_id": job["id"],
        "group_id": job["group_id"],
        "grant_id": job["grant_id"],
        "source_kind": "fixture",
        "started_at": now,
    }
    if denied:
        values.update(
            ended_at=now,
            coverage="none",
            partial_reasons=[],
            stop_reason="access_blocked",
            error_code="grant_unavailable",
        )
    conn.execute(insert(capture_runs).values(**values))
    return run_id


def _grant_is_available(conn: Connection, job: dict[str, Any], now: datetime) -> bool:
    row = (
        conn.execute(
            select(
                groups.c.source,
                source_grants.c.source_kind,
                source_grants.c.scopes,
                source_grants.c.valid_from,
                source_grants.c.valid_until,
                source_grants.c.revoked_at,
            )
            .select_from(source_grants.join(groups, groups.c.id == source_grants.c.group_id))
            .where(
                source_grants.c.id == job["grant_id"],
                source_grants.c.group_id == job["group_id"],
            )
            .with_for_update(read=True)
        )
        .mappings()
        .one_or_none()
    )
    return bool(
        row is not None
        and row["source"] == "fixture"
        and row["source_kind"] == "fixture"
        and row["revoked_at"] is None
        and row["valid_from"] <= now
        and (row["valid_until"] is None or row["valid_until"] > now)
        and "capture" in row["scopes"]
    )


def _run_failed_gap(conn: Connection, job: dict[str, Any], now: datetime) -> None:
    if job["kind"] != "discover":
        return
    group = (
        conn.execute(
            select(groups.c.enabled, groups.c.poll_minutes).where(groups.c.id == job["group_id"])
        )
        .mappings()
        .one_or_none()
    )
    if group is None or not group["enabled"] or group["poll_minutes"] not in (30, 60):
        return
    gap_from = _scheduled_slot(job)
    _insert_gap(
        conn,
        group_id=cast(UUID, job["group_id"]),
        gap_from=gap_from,
        gap_to=gap_from + timedelta(minutes=cast(int, group["poll_minutes"])),
        reason="run_failed",
        detected_at=now,
        compensated_by_job_id=None,
    )


def _scheduled_slot(job: dict[str, Any]) -> datetime:
    logical_key = cast(str, job["logical_key"])
    parts = logical_key.split(":", 2)
    if len(parts) == 3 and parts[0] == "discover":
        try:
            return datetime.fromisoformat(parts[2].replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            pass
    return cast(datetime, job["due_at"])


def _reclaim_expired(conn: Connection, now: datetime, settings: SchedulerSettings) -> None:
    expired = (
        conn.execute(
            select(jobs)
            .where(
                jobs.c.kind.in_(BROWSER_JOB_KINDS),
                jobs.c.state == "leased",
                jobs.c.lease_until <= now,
            )
            .order_by(jobs.c.lease_until, jobs.c.id)
            .with_for_update(skip_locked=True)
        )
        .mappings()
        .all()
    )
    for row in expired:
        job = dict(row)
        retry_count = cast(int, job["retry_count"])
        if retry_count < settings.max_retries:
            backoff_index = min(retry_count, len(settings.backoff_minutes) - 1)
            due_at = now + timedelta(minutes=settings.backoff_minutes[backoff_index])
            conn.execute(
                update(jobs)
                .where(jobs.c.id == job["id"], jobs.c.state == "leased")
                .values(
                    state="queued",
                    retry_count=retry_count + 1,
                    due_at=due_at,
                    lease_token=None,
                    lease_until=None,
                )
            )
        else:
            conn.execute(
                update(jobs)
                .where(jobs.c.id == job["id"], jobs.c.state == "leased")
                .values(state="failed", lease_token=None, lease_until=None)
            )
            conn.execute(
                update(capture_runs)
                .where(capture_runs.c.job_id == job["id"])
                .values(
                    ended_at=now,
                    coverage="none",
                    partial_reasons=[],
                    error_code="adapter_error",
                )
            )
            _run_failed_gap(conn, job, now)


def claim_next(
    conn: Connection,
    now: datetime,
    settings: SchedulerSettings = SchedulerSettings(),
) -> JobClaim | None:
    """Lease the next due job; this call commits before returning a claim."""
    normalized_now = _utc(now)
    if conn.in_transaction():
        raise RuntimeError("claim_next requires a Connection with no active transaction")
    with conn.begin():
        lock_acquired: bool = conn.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"), {"lock_key": _CLAIM_LOCK}
        ).scalar_one()
        if not lock_acquired:
            return None
        _reclaim_expired(conn, normalized_now, settings)
        active = conn.execute(
            select(jobs.c.id)
            .where(
                jobs.c.kind.in_(BROWSER_JOB_KINDS),
                jobs.c.state == "leased",
                jobs.c.lease_until > normalized_now,
            )
            .limit(1)
        ).scalar_one_or_none()
        if active is not None:
            return None

        for kind in ("discover", "revisit"):
            while True:
                row = (
                    conn.execute(
                        select(jobs)
                        .where(
                            jobs.c.kind == kind,
                            jobs.c.state == "queued",
                            jobs.c.due_at <= normalized_now,
                        )
                        .order_by(jobs.c.due_at, jobs.c.id)
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    break
                job = dict(row)
                if not _grant_is_available(conn, job, normalized_now):
                    _ensure_run(conn, job, normalized_now, denied=True)
                    conn.execute(
                        update(jobs)
                        .where(jobs.c.id == job["id"], jobs.c.state == "queued")
                        .values(state="cancelled", lease_token=None, lease_until=None)
                    )
                    continue

                token = uuid4()
                lease_until = normalized_now + timedelta(minutes=settings.lease_minutes)
                conn.execute(
                    update(jobs)
                    .where(jobs.c.id == job["id"], jobs.c.state == "queued")
                    .values(state="leased", lease_token=token, lease_until=lease_until)
                )
                run_id = _ensure_run(conn, job, normalized_now)
                return JobClaim(
                    job_id=cast(UUID, job["id"]),
                    group_id=cast(UUID, job["group_id"]),
                    grant_id=cast(UUID, job["grant_id"]),
                    run_id=run_id,
                    kind=cast(str, job["kind"]),
                    logical_key=cast(str, job["logical_key"]),
                    target_key=cast(str, job["target_key"]),
                    due_at=cast(datetime, job["due_at"]),
                    lease_token=token,
                    lease_until=lease_until,
                )
    return None


def _owned_job(conn: Connection, job_id: UUID, lease_token: UUID, now: datetime) -> dict[str, Any]:
    row = (
        conn.execute(
            select(jobs)
            .where(
                jobs.c.id == job_id,
                jobs.c.state == "leased",
                jobs.c.lease_token == lease_token,
                jobs.c.lease_until > now,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise QueueError("lease_lost", "The current lease token does not own this job")
    return dict(row)


def renew_lease(
    conn: Connection,
    job_id: UUID,
    lease_token: UUID,
    now: datetime,
    settings: SchedulerSettings = SchedulerSettings(),
) -> datetime:
    """Extend a lease only while the supplied token still owns it."""
    normalized_now = _utc(now)
    with atomic(conn):
        _owned_job(conn, job_id, lease_token, normalized_now)
        lease_until = normalized_now + timedelta(minutes=settings.lease_minutes)
        conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.lease_token == lease_token)
            .values(lease_until=lease_until)
        )
        return lease_until


def complete(conn: Connection, job_id: UUID, lease_token: UUID, now: datetime) -> None:
    """Complete a job only for the worker holding its unexpired lease."""
    normalized_now = _utc(now)
    with atomic(conn):
        _owned_job(conn, job_id, lease_token, normalized_now)
        conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.lease_token == lease_token)
            .values(state="succeeded", lease_token=None, lease_until=None)
        )


def cancel_claim(
    conn: Connection,
    job_id: UUID,
    lease_token: UUID,
    now: datetime,
    error_code: str = "grant_unavailable",
) -> None:
    """Cancel an owned run when its grant becomes unavailable during capture."""
    normalized_now = _utc(now)
    if error_code not in ERROR_CODES:
        raise ValueError(f"Unsupported error_code: {error_code}")
    with atomic(conn):
        _owned_job(conn, job_id, lease_token, normalized_now)
        conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.lease_token == lease_token)
            .values(state="cancelled", lease_token=None, lease_until=None)
        )
        conn.execute(
            update(capture_runs)
            .where(capture_runs.c.job_id == job_id)
            .values(
                ended_at=normalized_now,
                coverage="none",
                partial_reasons=[],
                stop_reason="access_blocked",
                error_code=error_code,
            )
        )


def fail(
    conn: Connection,
    job_id: UUID,
    lease_token: UUID,
    now: datetime,
    error_code: str = "adapter_error",
) -> None:
    """Fail a leased run and record one run_failed gap for its scheduled slot."""
    normalized_now = _utc(now)
    if error_code not in ERROR_CODES:
        raise ValueError(f"Unsupported error_code: {error_code}")
    with atomic(conn):
        job = _owned_job(conn, job_id, lease_token, normalized_now)
        conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.lease_token == lease_token)
            .values(state="failed", lease_token=None, lease_until=None)
        )
        conn.execute(
            update(capture_runs)
            .where(capture_runs.c.job_id == job_id)
            .values(
                ended_at=normalized_now,
                coverage="none",
                partial_reasons=[],
                error_code=error_code,
            )
        )
        _run_failed_gap(conn, job, normalized_now)
