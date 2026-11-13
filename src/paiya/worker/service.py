"""Single-cycle and continuous runners for synthetic discovery and revisit jobs."""

import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import SQLAlchemyError

from paiya.collector.errors import AdapterError
from paiya.collector.feed_script import FeedScriptAdapter
from paiya.collector.revisit_run import execute_revisit
from paiya.collector.revisit_script import RevisitScriptAdapter
from paiya.collector.run import execute_discovery
from paiya.inbox.retention import prune_collector_runs
from paiya.inbox.service import InboxError
from paiya.purge.errors import PurgeError
from paiya.purge.startup import check_purge_log
from paiya.scheduler.models import JobClaim
from paiya.scheduler.queue import claim_next, schedule_due
from paiya.scheduler.settings import SchedulerSettings
from paiya.settings import get_database_engine
from paiya.store.fixture_scope import FixtureScope
from paiya.store.tables import capture_gaps, groups, jobs
from paiya.worker.faults import FaultConfigurationError, validate_fault_configuration

_POLL_SECONDS = 5.0


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Worker time must include a UTC offset")
    return value.astimezone(UTC)


def _slot(now: datetime, poll_minutes: int) -> datetime:
    interval_seconds = poll_minutes * 60
    timestamp = int(now.timestamp())
    return datetime.fromtimestamp(timestamp - timestamp % interval_seconds, tz=UTC)


def _media_root() -> Path:
    raw_path = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
    if not raw_path:
        raise RuntimeError("PAIYA_FIXTURE_MEDIA_ROOT is required for worker run")
    root = Path(raw_path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _feed_root() -> Path:
    raw_path = os.environ.get("PAIYA_FIXTURE_FEED_DIR")
    if not raw_path:
        raise RuntimeError("PAIYA_FIXTURE_FEED_DIR is required for worker run")
    root = Path(raw_path).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("PAIYA_FIXTURE_FEED_DIR must name an existing directory")
    return root


def _scenario_path(root: Path, group_key: str) -> Path:
    path = (root / group_key).resolve()
    if not path.is_relative_to(root):
        raise AdapterError("adapter_error", "Fixture group path must stay inside its directory")
    return path


def _first_feed_label(feed_path: Path) -> str:
    try:
        document = json.loads(feed_path.read_text(encoding="utf-8"))
        runs = document["runs"]
        label = runs[0]["label"]
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
        raise AdapterError("adapter_error", "Could not select a local feed run") from error
    if not isinstance(label, str):
        raise AdapterError("adapter_error", "Feed run label must be text")
    return label


def _load_staged_assets(
    scenario_root: Path, envelopes: Sequence[Mapping[str, Any] | None]
) -> dict[str, bytes]:
    assets: dict[str, bytes] = {}
    asset_root = (scenario_root / "assets").resolve()
    for envelope in envelopes:
        if envelope is None:
            continue
        metadata = envelope.get("assets", {})
        if not isinstance(metadata, dict):
            raise AdapterError("adapter_error", "Fixture asset metadata must be an object")
        for meta in metadata.values():
            if not isinstance(meta, dict) or not isinstance(meta.get("staging_key"), str):
                raise AdapterError("adapter_error", "Fixture staging key is invalid")
            staging_key = cast(str, meta["staging_key"])
            relative = PurePosixPath(staging_key)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise AdapterError("adapter_error", "Fixture staging key must stay in assets")
            asset_path = (asset_root / Path(*relative.parts)).resolve()
            if not asset_path.is_relative_to(asset_root):
                raise AdapterError("adapter_error", "Fixture staging key must stay in assets")
            try:
                data = asset_path.read_bytes()
            except OSError as error:
                raise AdapterError(
                    "adapter_error", "Could not read a local staged asset"
                ) from error
            previous = assets.get(staging_key)
            if previous is not None and previous != data:
                raise AdapterError("adapter_error", "Fixture staging key has conflicting bytes")
            assets[staging_key] = data
    return assets


def _record_missed_slots(engine: Engine, now: datetime) -> None:
    """Record missed intervals after a successful slot even when no old job stayed queued."""
    with engine.connect() as connection, connection.begin():
        group_rows = (
            connection.execute(
                select(groups.c.id, groups.c.poll_minutes)
                .where(
                    groups.c.source == "fixture",
                    groups.c.enabled.is_(True),
                    groups.c.poll_minutes.in_((30, 60)),
                )
                .order_by(groups.c.id)
                .with_for_update()
            )
            .mappings()
            .all()
        )
        for group in group_rows:
            group_id = cast(UUID, group["id"])
            poll_minutes = cast(int, group["poll_minutes"])
            slot = _slot(now, poll_minutes)
            slot_key = slot.isoformat(timespec="seconds").replace("+00:00", "Z")
            logical_key = f"discover:{group_id}:{slot_key}"
            current_job_id = connection.execute(
                select(jobs.c.id).where(jobs.c.logical_key == logical_key)
            ).scalar_one_or_none()
            if current_job_id is None:
                continue
            previous = connection.execute(
                select(jobs.c.due_at)
                .where(
                    jobs.c.group_id == group_id,
                    jobs.c.kind == "discover",
                    jobs.c.state == "succeeded",
                    jobs.c.due_at < slot,
                )
                .order_by(jobs.c.due_at.desc(), jobs.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if previous is None:
                continue
            gap_from = cast(datetime, previous).astimezone(UTC) + timedelta(minutes=poll_minutes)
            if gap_from >= slot:
                continue
            recorded = connection.execute(
                select(capture_gaps.c.id).where(
                    capture_gaps.c.group_id == group_id,
                    capture_gaps.c.reason == "missed_slots",
                    capture_gaps.c.compensated_by_job_id == current_job_id,
                )
            ).scalar_one_or_none()
            if recorded is None:
                connection.execute(
                    insert(capture_gaps).values(
                        id=uuid4(),
                        group_id=group_id,
                        gap_from=gap_from,
                        gap_to=slot,
                        reason="missed_slots",
                        detected_at=now,
                        compensated_by_job_id=current_job_id,
                    )
                )


def _claim_one(engine: Engine, now: datetime) -> tuple[JobClaim | None, str | None]:
    settings = SchedulerSettings()
    with engine.connect() as connection:
        schedule_due(connection, now, settings)
    _record_missed_slots(engine, now)
    with engine.connect() as connection:
        claim = claim_next(connection, now, settings)
        if claim is None:
            return None, None
        group_key: str = cast(
            str,
            connection.execute(
                select(groups.c.group_key).where(groups.c.id == claim.group_id)
            ).scalar_one(),
        )
        return claim, group_key


def _run_discovery(
    engine: Engine,
    claim: JobClaim,
    group_key: str,
    now: datetime,
    feed_root: Path,
    media_root: Path,
) -> None:
    scenario = _scenario_path(feed_root, group_key)
    feed_path = scenario / "feed.json"
    adapter = FeedScriptAdapter(feed_path)
    run_label = _first_feed_label(feed_path)
    settings = SchedulerSettings()
    feed_run = adapter.read_run(group_key, run_label, settings.cards_per_step)
    assets = _load_staged_assets(
        scenario,
        [card.envelope for batch in feed_run.batches for card in batch],
    )
    scope = FixtureScope(claim.group_id, claim.grant_id, group_key)
    execute_discovery(
        engine,
        scope,
        claim,
        adapter,
        run_label,
        now,
        settings,
        staged_assets=assets,
        media_root=media_root,
    )


def _run_revisit(
    engine: Engine,
    claim: JobClaim,
    group_key: str,
    now: datetime,
    feed_root: Path,
    media_root: Path,
) -> None:
    configured_root = os.environ.get("PAIYA_FIXTURE_REVISIT_DIR")
    revisit_root = Path(configured_root).expanduser().resolve() if configured_root else feed_root
    scenario = _scenario_path(revisit_root, group_key)
    script_path = scenario / "revisits.json"
    adapter = RevisitScriptAdapter(script_path)
    visit = adapter.read_visit(group_key, claim.target_key, claim.due_at)
    assets = _load_staged_assets(scenario, [visit.envelope])
    scope = FixtureScope(claim.group_id, claim.grant_id, group_key)
    execute_revisit(
        engine,
        scope,
        claim,
        adapter,
        now,
        staged_assets=assets,
        media_root=media_root,
    )


def run_cycle(engine: Engine, now: datetime) -> dict[str, object]:
    """Schedule work and execute at most one leased fixture job."""
    clock = _utc(now)
    feed_root = _feed_root()
    media_root = _media_root()
    claim, group_key = _claim_one(engine, clock)
    if claim is None or group_key is None:
        return {"state": "idle"}
    if claim.kind == "discover":
        _run_discovery(engine, claim, group_key, clock, feed_root, media_root)
    elif claim.kind == "revisit":
        _run_revisit(engine, claim, group_key, clock, feed_root, media_root)
    else:
        raise AdapterError("adapter_error", "Worker received an unsupported job kind")
    return {
        "state": "succeeded",
        "job_id": str(claim.job_id),
        "run_id": str(claim.run_id),
        "kind": claim.kind,
    }


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def _startup_error(error: Exception) -> int:
    code = getattr(error, "code", "worker_startup_refused")
    print(f"{code}: {error}", file=sys.stderr, flush=True)
    return 2


def _prune_collector(engine: Engine) -> None:
    if prune_collector_runs(engine)["state"] != "succeeded":
        raise InboxError("collector_retention_failed", "Collector expiry requires purge replay")


def main(*, once: bool, now: datetime | None = None) -> int:
    """Run one fixture job or keep polling until stopped by the owner."""
    engine: Engine | None = None
    try:
        validate_fault_configuration()
        engine = get_database_engine()
        check_purge_log(engine)
        if now is not None and not once:
            raise ValueError("--now is only valid with --once")
        retention_due = 0.0
        if once:
            if os.environ.get("PAIYA_COLLECTOR_INBOX"):
                _prune_collector(engine)
            _print_json({"ok": True, "result": run_cycle(engine, now or datetime.now(UTC))})
            return 0
        while True:
            if os.environ.get("PAIYA_COLLECTOR_INBOX") and time.monotonic() >= retention_due:
                _prune_collector(engine)
                retention_due = time.monotonic() + 60
            result = run_cycle(engine, datetime.now(UTC))
            if result["state"] != "idle":
                _print_json({"ok": True, "result": result})
            time.sleep(_POLL_SECONDS)
    except AdapterError as error:
        _print_json({"ok": False, "error": {"code": error.code, "message": "Fixture run failed"}})
        return 1
    except (FaultConfigurationError, InboxError, PurgeError, RuntimeError, ValueError) as error:
        return _startup_error(error)
    except SQLAlchemyError:
        _print_json(
            {
                "ok": False,
                "error": {
                    "code": "database_error",
                    "message": "Worker database operation failed",
                },
            }
        )
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception:
        _print_json(
            {"ok": False, "error": {"code": "worker_cycle_failed", "message": "Fixture run failed"}}
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()
