"""Expire Collector runs through the replayable Paiya purge path."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Engine, select

from paiya.grants.collector import GROUP_KEY, RETENTION, SOURCE_KIND
from paiya.inbox.media import recover_media
from paiya.inbox.service import (
    _directory,
    _finish,
    _receipt,
    _rejected,
    collector_inbox_lock,
    inbox_paths,
    media_journal,
)
from paiya.purge.service import run_purge
from paiya.purge.startup import check_purge_log
from paiya.store.tables import capture_runs, groups


def _bundle_started(path: Path, run_id: UUID) -> datetime:
    """Use run metadata independently of evidence integrity and bound it by local age."""
    ready = path / "READY"
    stamp = ready if ready.is_file() and not ready.is_symlink() else path
    published = datetime.fromtimestamp(stamp.stat().st_mtime, UTC)
    manifest = path / "run.json"
    if manifest.is_symlink() or manifest.is_junction() or not manifest.is_file():
        return published
    try:
        with manifest.open("rb") as stream:
            value = json.loads(stream.read(65537))
        if value["run_id"] == str(run_id) and value["group_key"] == GROUP_KEY:
            started = datetime.fromisoformat(value["started_at"])
            if started.tzinfo is not None:
                return min(started, published)
    except (ValueError, KeyError, TypeError):
        pass
    return published


def prune_collector_runs(engine: Engine) -> dict[str, object]:
    """Purge expired database content and published or claimed inbox bundles."""
    inbox, media = inbox_paths()
    cutoff = datetime.now(UTC) - RETENTION
    expired_bundles = 0
    with collector_inbox_lock(engine) as conn:
        # Collector owns its hidden unpublished directories. Paiya owns UUID claims.
        for stage in ("processing", "incoming"):
            folder = _directory(inbox / stage)
            if not folder.is_dir():
                continue
            for candidate in folder.iterdir():
                try:
                    run_id = UUID(candidate.name)
                except ValueError:
                    continue
                if (
                    str(run_id) != candidate.name
                    or candidate.is_symlink()
                    or candidate.is_junction()
                ):
                    continue
                if not candidate.is_dir():
                    continue
                try:
                    if _bundle_started(candidate, run_id) > cutoff:
                        continue
                except FileNotFoundError:
                    continue
                rejected = _directory(inbox / "rejected")
                rejected.mkdir(exist_ok=True)
                result = _rejected(run_id, "run_expired")
                if stage == "processing":
                    recover_media(conn, media, media_journal(inbox, run_id))
                    _finish(inbox, candidate, result)
                else:
                    target = _directory(rejected / candidate.name)
                    if not (target / "result.json").is_file():
                        _receipt(target, result)
                    try:
                        shutil.rmtree(candidate)
                    except FileNotFoundError:
                        if candidate.exists():
                            raise
                expired_bundles += 1

        check_purge_log(engine)
        run_ids: list[UUID] = list(
            conn.execute(
                select(capture_runs.c.id)
                .join(groups, groups.c.id == capture_runs.c.group_id)
                .where(
                    capture_runs.c.source_kind == SOURCE_KIND,
                    groups.c.source == SOURCE_KIND,
                    groups.c.group_key == GROUP_KEY,
                    capture_runs.c.started_at <= cutoff,
                )
                .order_by(capture_runs.c.started_at, capture_runs.c.id)
            ).scalars()
        )
        conn.commit()
        results = [run_purge(engine, f"collector_run:{run_id}") for run_id in run_ids]

    complete = all(
        result["state"] == "succeeded"
        and not cast(dict[str, Any], result["result_counts"])["files_pending"]
        for result in results
    )
    return {
        "state": "succeeded" if complete else "partial_failure",
        "expired_bundles": expired_bundles,
        "purges": results,
    }
