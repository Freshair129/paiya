"""Claim and persist a local Collector bundle without visiting its source."""

import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import Connection, Engine, insert, select, text, update

from paiya.grants.collector import GROUP_KEY, RETENTION, SOURCE_KIND, verify_collector_grant
from paiya.grants.errors import GrantError
from paiya.inbox.validation import ValidatedRun, read_inbox
from paiya.ingest.service import CaptureConflict, persist_collector_observation
from paiya.purge.startup import check_purge_log
from paiya.store.tables import capture_gaps, capture_runs, jobs, purge_jobs


class InboxError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _directory(path: Path) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    for entry in (absolute, *absolute.parents):
        if entry.is_symlink() or entry.is_junction():
            raise InboxError("linked_path", "Collector paths must not traverse links")
    return absolute


def inbox_paths() -> tuple[Path, Path]:
    values = [
        os.environ.get(name)
        for name in (
            "PAIYA_COLLECTOR_INBOX",
            "PAIYA_FIXTURE_MEDIA_ROOT",
            "PAIYA_PURGE_LOG_PATH",
        )
    ]
    if not all(values):
        raise InboxError("inbox_not_configured", "Configure the inbox, media root and purge log")
    inbox, media, log = (_directory(Path(cast(str, value))) for value in values)
    repo = Path(__file__).resolve().parents[3]
    if (
        any(
            path == inbox or path in inbox.parents or inbox in path.parents
            for path in (repo, media)
        )
        or log == inbox
        or inbox in log.parents
        or log == media
        or media in log.parents
    ):
        raise InboxError(
            "inbox_path_overlap", "Inbox must be separate from source, media and purge log"
        )
    if not inbox.is_dir() or not media.is_dir() or not log.parent.is_dir() or log.is_dir():
        raise InboxError("inbox_path_unavailable", "Create the configured directories first")
    _directory(media / "assets")
    return inbox, media


def _rejected(run_id: UUID, code: str) -> dict[str, Any]:
    return {"run_id": str(run_id), "state": "rejected", "code": code}


def _persist(conn: Connection, run_id: UUID, bundle: ValidatedRun, media: Path) -> dict[str, Any]:
    summary = bundle.summary
    started = datetime.fromisoformat(summary["started_at"])
    ended = datetime.fromisoformat(summary["ended_at"])
    grant_id = UUID(summary["grant_id"])
    group_key = cast(str, summary["group_key"])
    if started <= datetime.now(UTC) - RETENTION:
        return _rejected(run_id, "run_expired")
    if ended > datetime.now(UTC):
        return _rejected(run_id, "run_in_future")
    group_id = verify_collector_grant(conn, grant_id, group_key, started)
    if summary["coverage"] == "none" and bundle.envelopes:
        return _rejected(run_id, "validation_failed")
    for document in bundle.envelopes:
        seller = document["payload"].get("seller")
        if seller and seller.get("identity_scope") not in (None, GROUP_KEY):
            return _rejected(run_id, "identity_scope_mismatch")

    job_id = uuid5(run_id, "paiya:collector:inbox")
    conn.execute(
        insert(jobs).values(
            id=job_id,
            group_id=group_id,
            grant_id=grant_id,
            kind=summary["job_kind"],
            logical_key=f"collector:{run_id}",
            target_key=str(run_id),
            due_at=started,
            state="failed" if summary["coverage"] == "none" else "succeeded",
        )
    )
    conn.execute(
        insert(capture_runs).values(
            id=run_id,
            job_id=job_id,
            group_id=group_id,
            grant_id=grant_id,
            source_kind=SOURCE_KIND,
            started_at=started,
            ended_at=ended,
            **{
                field: summary[field]
                for field in (
                    "session_result",
                    "sort_result",
                    "coverage",
                    "partial_reasons",
                    "stop_reason",
                    "cards_seen",
                    "cards_parsed",
                    "scroll_steps",
                    "error_code",
                )
            },
        )
    )
    if summary["coverage"] == "none" and ended > started:
        conn.execute(
            insert(capture_gaps).values(
                id=uuid5(run_id, "paiya:collector:gap"),
                group_id=group_id,
                gap_from=started,
                gap_to=ended,
                reason="run_failed",
                detected_at=ended,
            )
        )

    results = []
    for document in bundle.envelopes:
        capture_id = document["capture_id"]
        try:
            with conn.begin_nested():
                observation_id = persist_collector_observation(
                    conn,
                    group_id,
                    grant_id,
                    group_key,
                    document,
                    bundle.assets,
                    media,
                )
        except CaptureConflict:
            results.append({"capture_id": capture_id, "state": "capture_conflict"})
        else:
            results.append(
                {
                    "capture_id": capture_id,
                    "state": "ingested",
                    "observation_id": str(observation_id),
                }
            )

    result = {"run_id": str(run_id), "state": "done", "envelopes": results}
    conn.execute(update(jobs).where(jobs.c.id == job_id).values(result=result))
    verify_collector_grant(conn, grant_id, group_key, started, lock=True)
    if started <= datetime.now(UTC) - RETENTION:
        raise InboxError("run_expired", "Run reached its retention limit before commit")
    return result


def _receipt(path: Path, result: dict[str, Any]) -> None:
    """Write a content-free receipt before removing the claimed copy of the payload."""
    path.mkdir(exist_ok=True)
    pending = _directory(path / ".result.tmp")
    pending.unlink(missing_ok=True)
    with pending.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    pending.replace(_directory(path / "result.json"))


def _finish(inbox: Path, processing: Path, result: dict[str, Any]) -> dict[str, Any]:
    target = _directory(inbox / result["state"] / processing.name)
    if result.get("code") != "run_duplicate" or not target.exists():
        _receipt(target, result)
    shutil.rmtree(processing)
    return result


def ingest_inbox_run(engine: Engine, run_id: UUID) -> dict[str, Any]:
    inbox, media = inbox_paths()
    check_purge_log(engine)
    name = str(run_id)
    incoming = _directory(inbox / "incoming" / name)
    processing = _directory(inbox / "processing" / name)

    with collector_inbox_lock(engine) as conn:
        for stage in ("processing", "done", "rejected"):
            _directory(inbox / stage).mkdir(exist_ok=True)
        done = _directory(inbox / "done" / name / "result.json")
        rejected = _directory(inbox / "rejected" / name / "result.json")
        fresh = not processing.exists()
        if fresh:
            if not incoming.is_dir() or not _directory(incoming / "READY").is_file():
                raise InboxError("run_unavailable", "A READY inbox run was not found")
            incoming.rename(processing)
        elif not processing.is_dir():
            raise InboxError("run_unavailable", "The processing run must be a directory")

        if not fresh and (done.is_file() or rejected.is_file()):
            receipt = rejected if rejected.is_file() else done
            result = json.loads(receipt.read_text(encoding="utf-8"))
            shutil.rmtree(processing)
            return cast(dict[str, Any], result)

        try:
            with conn.begin():
                previous = (
                    conn.execute(
                        select(capture_runs.c.source_kind, jobs.c.result)
                        .join(jobs, jobs.c.id == capture_runs.c.job_id)
                        .where(capture_runs.c.id == run_id)
                    )
                    .mappings()
                    .one_or_none()
                )
                purged = (
                    conn.execute(
                        select(purge_jobs.c.id)
                        .where(
                            purge_jobs.c.scope_kind == "collector_run",
                            purge_jobs.c.scope_key == name,
                        )
                        .limit(1)
                    ).first()
                    is not None
                )
                if purged or (
                    fresh and (previous is not None or done.is_file() or rejected.is_file())
                ):
                    result = _rejected(run_id, "run_duplicate")
                elif previous is not None:
                    result = (
                        cast(dict[str, Any], previous["result"])
                        if previous["source_kind"] == SOURCE_KIND and previous["result"]
                        else _rejected(run_id, "run_duplicate")
                    )
                else:
                    report, bundle = read_inbox(processing)
                    if bundle is None:
                        result = _rejected(run_id, "validation_failed")
                        result["problems"] = report["problems"]
                    else:
                        result = _persist(conn, run_id, bundle, media)
        except (GrantError, InboxError) as error:
            result = _rejected(run_id, error.code)
        return _finish(inbox, processing, result)


@contextmanager
def collector_inbox_lock(engine: Engine) -> Iterator[Connection]:
    """Serialize claims and Collector maintenance for one database."""
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": 793550218169004731})
        conn.commit()
        try:
            yield conn
        finally:
            if conn.in_transaction():
                conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": 793550218169004731})
            conn.commit()
