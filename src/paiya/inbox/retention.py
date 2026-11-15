"""Expire Collector runs through the replayable Paiya purge path."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Connection, Engine, select

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
from paiya.store.tables import capture_runs, groups, source_grants


def _bundle_summary(path: Path, run_id: UUID) -> dict[str, Any]:
    manifest = path / "run.json"
    if manifest.is_symlink() or manifest.is_junction() or not manifest.is_file():
        return {}
    try:
        with manifest.open("rb") as stream:
            value = json.loads(stream.read(65537))
        if isinstance(value, dict) and value.get("run_id") == str(run_id):
            return cast(dict[str, Any], value)
    except (ValueError, TypeError):
        pass
    return {}


def _bundle_started(path: Path, summary: dict[str, Any]) -> datetime:
    """Use run metadata independently of evidence integrity and bound it by local age."""
    ready = path / "READY"
    if not ready.is_symlink() and ready.is_file():
        modified = ready.stat().st_mtime
    else:
        modified = path.stat().st_mtime
        for name in ("assets", "envelopes"):
            folder = path / name
            if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
                continue
            for file in folder.iterdir():
                if not file.is_symlink() and file.is_file():
                    modified = min(modified, file.stat().st_mtime)
    published = datetime.fromtimestamp(modified, UTC)
    try:
        started = datetime.fromisoformat(summary["started_at"])
        if started.tzinfo is not None and started.utcoffset() == UTC.utcoffset(started):
            return min(started, published)
    except (ValueError, KeyError, TypeError):
        pass
    return published


def _remove_pending(
    conn: Connection,
    inbox: Path,
    media: Path,
    *,
    cutoff: datetime | None = None,
    revoked_grants: set[str] | None = None,
) -> tuple[int, int]:
    expired = revoked = 0
    rejected = _directory(inbox / "rejected")
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
            if str(run_id) != candidate.name or candidate.is_symlink() or candidate.is_junction():
                continue
            if not candidate.is_dir():
                continue
            try:
                summary = _bundle_summary(candidate, run_id)
                group_key = summary.get("group_key")
                if group_key not in (None, GROUP_KEY):
                    continue
                target = _directory(rejected / candidate.name)
                receipt = _directory(target / "result.json")
                finalized = receipt.is_file()
                if stage == "incoming" and not finalized:
                    ready = candidate / "READY"
                    if ready.is_symlink() or not ready.is_file():
                        continue
                    if group_key is None and not all(
                        (candidate / name).is_dir() for name in ("assets", "envelopes")
                    ):
                        continue
                grant_id = summary.get("grant_id")
                is_revoked = (
                    group_key == GROUP_KEY
                    and isinstance(grant_id, str)
                    and revoked_grants is not None
                    and grant_id.casefold() in revoked_grants
                )
                if not is_revoked and not (
                    cutoff is not None
                    and (finalized or _bundle_started(candidate, summary) <= cutoff)
                ):
                    continue
            except FileNotFoundError:
                continue
            rejected.mkdir(exist_ok=True)
            result = _rejected(run_id, "grant_unavailable" if is_revoked else "run_expired")
            if stage == "processing":
                recover_media(conn, media, media_journal(inbox, run_id))
                if not finalized:
                    _finish(inbox, candidate, result)
                else:
                    shutil.rmtree(candidate)
            else:
                if not finalized:
                    _receipt(target, result)
                try:
                    shutil.rmtree(candidate)
                except FileNotFoundError:
                    if candidate.exists():
                        raise
            if is_revoked:
                revoked += 1
            else:
                expired += 1
    return expired, revoked


def clean_pending_for_grant_purge(engine: Engine, scope: str) -> None:
    """Delete matching published and claimed copies before a revoked Collector grant purge."""
    if not scope.startswith("grant:"):
        return
    grant_id = UUID(scope.removeprefix("grant:"))
    with engine.connect() as conn:
        approved = conn.execute(
            select(source_grants.c.id)
            .join(groups, groups.c.id == source_grants.c.group_id)
            .where(
                source_grants.c.id == grant_id,
                source_grants.c.source_kind == SOURCE_KIND,
                groups.c.source == SOURCE_KIND,
                groups.c.group_key == GROUP_KEY,
                source_grants.c.revoked_at.is_not(None),
            )
        ).first()
    if approved is not None:
        inbox, media = inbox_paths()
        with collector_inbox_lock(engine) as conn:
            _remove_pending(conn, inbox, media, revoked_grants={str(grant_id)})


def prune_collector_runs(engine: Engine) -> dict[str, object]:
    """Purge expired database content and published or claimed inbox bundles."""
    inbox, media = inbox_paths()
    cutoff = datetime.now(UTC) - RETENTION
    with collector_inbox_lock(engine) as conn:
        check_purge_log(engine)
        revoked_ids: list[UUID] = list(
            conn.execute(
                select(source_grants.c.id)
                .join(groups, groups.c.id == source_grants.c.group_id)
                .where(
                    source_grants.c.source_kind == SOURCE_KIND,
                    groups.c.source == SOURCE_KIND,
                    groups.c.group_key == GROUP_KEY,
                    source_grants.c.revoked_at.is_not(None),
                )
            ).scalars()
        )
        conn.commit()
        expired_bundles, revoked_bundles = _remove_pending(
            conn, inbox, media, cutoff=cutoff, revoked_grants={str(value) for value in revoked_ids}
        )
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
        "revoked_bundles": revoked_bundles,
        "purges": results,
    }
