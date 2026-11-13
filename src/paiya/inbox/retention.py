"""Expire Collector runs through the replayable Paiya purge path."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Engine, select

from paiya.grants.collector import GROUP_KEY, RETENTION, SOURCE_KIND
from paiya.inbox.service import (
    InboxError,
    _directory,
    collector_inbox_lock,
    inbox_paths,
    ingest_inbox_run,
)
from paiya.inbox.validation import read_inbox
from paiya.purge.service import run_purge
from paiya.purge.startup import check_purge_log
from paiya.store.tables import capture_runs, groups


def prune_collector_runs(engine: Engine) -> dict[str, object]:
    """Purge expired database content and published inbox bundles."""
    inbox, _media = inbox_paths()
    check_purge_log(engine)
    cutoff = datetime.now(UTC) - RETENTION
    with collector_inbox_lock(engine) as conn:
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

    # Collector owns unpublished temporary directories and prunes them itself.
    # A claimed directory belongs to ingest and is recovered through the same path.
    expired_bundles = 0
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
            if not candidate.is_dir() or not (candidate / "READY").is_file():
                continue
            _, bundle = read_inbox(candidate)
            if bundle is None or bundle.summary["group_key"] != GROUP_KEY:
                continue
            if datetime.fromisoformat(bundle.summary["started_at"]) <= cutoff:
                try:
                    ingest_inbox_run(engine, run_id)
                except InboxError as error:
                    if error.code != "run_unavailable":
                        raise
                else:
                    expired_bundles += 1

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
