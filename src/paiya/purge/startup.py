"""Fail-closed startup check for commands that can expose or enrich data."""

import os
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from paiya.purge.errors import PurgeError
from paiya.purge.log import PurgeLogEntry, ensure_unique_purge_ids, iter_purge_log
from paiya.store.tables import purge_jobs

_QUERY_BATCH_SIZE = 1000


def _log_path_from_environment() -> Path | None:
    raw_path = os.environ.get("PAIYA_PURGE_LOG_PATH")
    if raw_path is None:
        return None
    try:
        return Path(raw_path).expanduser().resolve()
    except (OSError, RuntimeError) as error:
        raise PurgeError("purge_log_invalid", "The purge log path could not be resolved") from error


def count_pending_purge_ids(engine: Engine, entries: Iterable[PurgeLogEntry]) -> int:
    """Count distinct logged IDs without mutating the database."""
    entries_by_id = {entry.id: entry for entry in entries}
    purge_ids = set(entries_by_id)
    if not purge_ids:
        return 0

    succeeded: set[UUID] = set()
    ordered_ids = list(purge_ids)
    with engine.connect() as conn:
        with conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            for offset in range(0, len(ordered_ids), _QUERY_BATCH_SIZE):
                batch = ordered_ids[offset : offset + _QUERY_BATCH_SIZE]
                succeeded.update(
                    conn.execute(
                        select(purge_jobs.c.id).where(
                            purge_jobs.c.id.in_(batch), purge_jobs.c.state == "succeeded"
                        )
                    ).scalars()
                )
    return len(purge_ids - succeeded)


def check_purge_log(engine: Engine | None = None) -> None:
    """Refuse startup when a configured purge log is invalid or has pending IDs."""
    path = _log_path_from_environment()
    if path is None:
        return

    entries = ensure_unique_purge_ids(iter_purge_log(path, missing_ok=True))
    if not entries:
        return

    if engine is None:
        from paiya.settings import get_database_engine

        engine = get_database_engine()
    try:
        pending = count_pending_purge_ids(engine, entries)
    except SQLAlchemyError as error:
        raise PurgeError(
            "purge_state_unavailable", "The purge replay state could not be verified"
        ) from error
    if pending:
        raise PurgeError(
            "purge_replay_required",
            f"purge_replay_required: {pending} pending purge command(s)",
        )
