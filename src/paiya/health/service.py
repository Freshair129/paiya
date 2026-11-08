"""Aggregate count and timestamp health data without returning listing content."""

import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Engine, func, select

from paiya.backup.service import backup_status
from paiya.enrich.service import enrich_status
from paiya.logging import emit_event
from paiya.settings import get_disk_min_free_bytes, get_job_overdue_minutes
from paiya.store.tables import capture_gaps, capture_runs, evidence, groups, jobs

DiskUsage = tuple[int, int, int]
DiskUsageFn = Callable[[Path], DiskUsage]
_JOB_KINDS = ("discover", "revisit", "manual_import", "enrich", "purge")
_MEDIA_STATES = ("captured", "capture_failed")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("health check time must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _existing_path(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def _disk_snapshot(
    path: Path,
    *,
    path_kind: str,
    min_free_bytes: int,
    disk_usage_fn: DiskUsageFn,
) -> dict[str, object]:
    total_bytes, _used_bytes, free_bytes = disk_usage_fn(_existing_path(path))
    state = "low" if free_bytes < min_free_bytes else "ok"
    if state == "low":
        emit_event(
            "disk_low",
            path_kind=path_kind,
            free_bytes=free_bytes,
            min_free_bytes=min_free_bytes,
        )
    return {
        "free_bytes": free_bytes,
        "total_bytes": total_bytes,
        "min_free_bytes": min_free_bytes,
        "state": state,
    }


def _queue_health(engine: Engine, now: datetime, overdue_minutes: int) -> dict[str, object]:
    cutoff = now - timedelta(minutes=overdue_minutes)
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(
                    jobs.c.kind,
                    func.count().label("overdue_count"),
                    func.min(jobs.c.due_at).label("oldest_due_at"),
                )
                .where(
                    jobs.c.state == "queued",
                    jobs.c.due_at < cutoff,
                )
                .group_by(jobs.c.kind)
            )
            .mappings()
            .all()
        )
    health: dict[str, object] = {
        kind: {"overdue_count": 0, "oldest_overdue_age_minutes": None} for kind in _JOB_KINDS
    }
    for row in rows:
        kind = cast(str, row["kind"])
        oldest_due_at = cast(datetime, row["oldest_due_at"])
        health[kind] = {
            "overdue_count": cast(int, row["overdue_count"]),
            "oldest_overdue_age_minutes": max(
                0, int((now - oldest_due_at.astimezone(UTC)).total_seconds() / 60)
            ),
        }
    return health


def _coverage_health(engine: Engine, now: datetime) -> list[dict[str, object]]:
    recent_cutoff = now - timedelta(days=7)
    with engine.connect() as conn:
        group_ids: Sequence[UUID] = (
            conn.execute(
                select(groups.c.id).where(groups.c.source == "fixture").order_by(groups.c.id)
            )
            .scalars()
            .all()
        )
        result: list[dict[str, object]] = []
        for group_id in group_ids:
            last_successful_at = cast(
                datetime | None,
                conn.execute(
                    select(func.max(capture_runs.c.ended_at)).where(
                        capture_runs.c.group_id == group_id,
                        capture_runs.c.ended_at.is_not(None),
                        capture_runs.c.error_code.is_(None),
                        capture_runs.c.coverage.in_(("complete", "partial")),
                    )
                ).scalar_one(),
            )
            latest_coverage = conn.execute(
                select(capture_runs.c.coverage)
                .where(capture_runs.c.group_id == group_id, capture_runs.c.ended_at.is_not(None))
                .order_by(capture_runs.c.ended_at.desc(), capture_runs.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            capture_gap_count = conn.execute(
                select(func.count())
                .select_from(capture_gaps)
                .where(capture_gaps.c.group_id == group_id)
            ).scalar_one()
            latest_gap = (
                conn.execute(
                    select(
                        capture_gaps.c.gap_from,
                        capture_gaps.c.gap_to,
                        capture_gaps.c.reason,
                        capture_gaps.c.detected_at,
                    )
                    .where(
                        capture_gaps.c.group_id == group_id,
                        capture_gaps.c.detected_at >= recent_cutoff,
                    )
                    .order_by(capture_gaps.c.detected_at.desc(), capture_gaps.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            result.append(
                {
                    "group_id": str(group_id),
                    "last_successful_at": _iso(last_successful_at),
                    "latest_coverage": latest_coverage,
                    "capture_gap_count": capture_gap_count,
                    "latest_gap_7d": (
                        {
                            "gap_from": _iso(cast(datetime, latest_gap["gap_from"])),
                            "gap_to": _iso(cast(datetime, latest_gap["gap_to"])),
                            "reason": latest_gap["reason"],
                            "detected_at": _iso(cast(datetime, latest_gap["detected_at"])),
                        }
                        if latest_gap is not None
                        else None
                    ),
                }
            )
        return result


def _media_health(engine: Engine, now: datetime) -> dict[str, object]:
    cutoff = now - timedelta(hours=24)
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(evidence.c.media_state, func.count().label("count"))
                .where(
                    evidence.c.captured_at >= cutoff,
                    evidence.c.media_state.in_(_MEDIA_STATES),
                )
                .group_by(evidence.c.media_state)
            )
            .mappings()
            .all()
        )
        disk_low_count = conn.execute(
            select(func.count())
            .select_from(evidence)
            .where(
                evidence.c.captured_at >= cutoff,
                evidence.c.media_reason == "disk_low",
            )
        ).scalar_one()
    counts = {state: 0 for state in _MEDIA_STATES}
    for row in rows:
        counts[cast(str, row["media_state"])] = cast(int, row["count"])
    denominator = counts["captured"] + counts["capture_failed"]
    return {
        "captured": counts["captured"],
        "capture_failed": counts["capture_failed"],
        "success_rate": counts["captured"] / denominator if denominator else None,
        "disk_low": disk_low_count,
    }


def _backup_health(engine: Engine, now: datetime) -> dict[str, object]:
    status = backup_status(engine, now=now)
    last_success = cast(Mapping[str, Any] | None, status["last_success"])
    last_failure = cast(Mapping[str, Any] | None, status["last_failure"])
    completed_at = (
        datetime.fromisoformat(cast(str, last_success["completed_at"]).replace("Z", "+00:00"))
        if last_success
        else None
    )
    age_hours = (
        max(0.0, (now - completed_at.astimezone(UTC)).total_seconds() / 3600)
        if completed_at is not None
        else None
    )
    state = "never" if age_hours is None else "stale" if age_hours > 26 else "ok"
    return {
        "last_success_at": last_success["completed_at"] if last_success else None,
        "age_hours": age_hours,
        "state": state,
        "last_failure_at": last_failure["completed_at"] if last_failure else None,
        "last_failure_reason": last_failure["error_code"] if last_failure else None,
    }


def get_health_report(
    engine: Engine,
    *,
    now: datetime | None = None,
    disk_usage_fn: DiskUsageFn | None = None,
) -> dict[str, object]:
    """Build the shared API/CLI O2 response from persisted counts and timestamps."""
    checked_at = _utc(now or datetime.now(UTC))
    disk_usage = disk_usage_fn or shutil.disk_usage
    min_free_bytes = get_disk_min_free_bytes()
    media_root = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
    if not media_root:
        raise RuntimeError("PAIYA_FIXTURE_MEDIA_ROOT is required for the health report")

    disk: dict[str, object] = {
        "media": _disk_snapshot(
            Path(media_root),
            path_kind="media",
            min_free_bytes=min_free_bytes,
            disk_usage_fn=disk_usage,
        )
    }
    backup_root = os.environ.get("PAIYA_BACKUP_DIR")
    if backup_root:
        disk["backup"] = _disk_snapshot(
            Path(backup_root),
            path_kind="backup",
            min_free_bytes=min_free_bytes,
            disk_usage_fn=disk_usage,
        )

    return {
        "checked_at": _iso(checked_at),
        "queue": _queue_health(engine, checked_at, get_job_overdue_minutes()),
        "coverage": _coverage_health(engine, checked_at),
        "media": _media_health(engine, checked_at),
        "disk": disk,
        "backup": _backup_health(engine, checked_at),
        "ai_backlog": enrich_status(engine, now=checked_at),
    }
