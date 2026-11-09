"""Plan and apply conservative O1b backup retention."""

import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from paiya.backup.errors import BackupError
from paiya.backup.service import (
    _backup_configuration,
    _is_path_within,
    _load_verified_set,
)
from paiya.store.tables import backup_runs

_SET_NAME = re.compile(
    r"(?P<timestamp>\d{8}T\d{6}Z)-"
    r"(?P<run_id>[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\Z"
)
_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True)
class _BackupSet:
    path: Path
    name: str
    run_id: UUID
    created_at: datetime
    created_at_text: str
    size_bytes: int


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    )


def _tree_size(path: Path) -> int | None:
    total = 0
    pending = [path]
    try:
        while pending:
            current = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or bool(
                        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
                    ):
                        return None
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        total += info.st_size
                    else:
                        return None
    except OSError:
        return None
    return total


def _is_direct_child(root: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return _is_path_within(root, resolved) and os.path.normcase(
        str(resolved.parent)
    ) == os.path.normcase(str(root))


def _load_candidate(root: Path, candidate: Path) -> _BackupSet | None:
    match = _SET_NAME.fullmatch(candidate.name)
    if match is None or _is_link_or_reparse_point(candidate) or not candidate.is_dir():
        return None
    if not _is_direct_child(root, candidate):
        return None
    size_bytes = _tree_size(candidate)
    if size_bytes is None:
        return None
    try:
        manifest, _verification = _load_verified_set(candidate)
    except (BackupError, OSError, RuntimeError, ValueError):
        return None

    manifest_run_id = manifest.get("backup_run_id")
    created_at_text = manifest.get("created_at")
    if not isinstance(manifest_run_id, str) or not isinstance(created_at_text, str):
        return None
    try:
        run_id = UUID(match.group("run_id"))
        if UUID(manifest_run_id) != run_id:
            return None
        created_at = datetime.fromisoformat(created_at_text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if created_at.tzinfo is None or created_at.utcoffset() != UTC.utcoffset(created_at):
        return None
    created_at = created_at.astimezone(UTC)
    if created_at.strftime("%Y%m%dT%H%M%SZ") != match.group("timestamp"):
        return None
    return _BackupSet(
        path=candidate,
        name=candidate.name,
        run_id=run_id,
        created_at=created_at,
        created_at_text=created_at_text,
        size_bytes=size_bytes,
    )


def _backup_sets(root: Path) -> list[_BackupSet]:
    try:
        info = root.stat()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise BackupError(
            "backup_destination_invalid", "PAIYA_BACKUP_DIR is unavailable"
        ) from error
    if not stat.S_ISDIR(info.st_mode):
        raise BackupError("backup_destination_invalid", "PAIYA_BACKUP_DIR must be a directory")

    try:
        children = list(root.iterdir())
    except OSError as error:
        raise BackupError(
            "backup_destination_invalid", "PAIYA_BACKUP_DIR cannot be read"
        ) from error
    return [
        backup_set for child in children if (backup_set := _load_candidate(root, child)) is not None
    ]


def _database_protections(engine: Engine) -> tuple[set[str], str | None, str | None]:
    try:
        with engine.connect() as connection:
            running_rows = (
                connection.execute(
                    select(backup_runs.c.id, backup_runs.c.set_name).where(
                        backup_runs.c.state == "running"
                    )
                )
                .mappings()
                .all()
            )
            latest_success = (
                connection.execute(
                    select(backup_runs.c.id, backup_runs.c.set_name)
                    .where(backup_runs.c.state == "succeeded")
                    .order_by(backup_runs.c.completed_at.desc(), backup_runs.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
    except SQLAlchemyError as error:
        raise BackupError(
            "database_error", "The backup retention plan could not read backup_runs"
        ) from error

    running = {str(row["id"]) for row in running_rows}
    running.update(str(row["set_name"]) for row in running_rows if row["set_name"] is not None)
    if latest_success is None:
        return running, None, None
    return running, str(latest_success["id"]), cast(str | None, latest_success["set_name"])


def _item(backup_set: _BackupSet, reason: str) -> dict[str, object]:
    return {
        "set_name": backup_set.name,
        "created_at": backup_set.created_at_text,
        "reason": reason,
        "bytes": backup_set.size_bytes,
    }


def _fsync_parent(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_to_remove(root: Path, backup_set: _BackupSet) -> bool:
    if _is_link_or_reparse_point(root) or not root.is_dir():
        return False
    if _is_link_or_reparse_point(backup_set.path) or not _is_direct_child(root, backup_set.path):
        return False
    current = _load_candidate(root, backup_set.path)
    return current == backup_set


def _path_exists_without_following(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def prune_backup_sets(engine: Engine, *, apply: bool = False) -> dict[str, object]:
    """Return a seven-day/four-ISO-week plan and optionally delete its candidates."""
    backup_root, _media_root, _purge_log, _same_device_allowed = _backup_configuration(
        create_directory=False
    )
    backup_sets = _backup_sets(backup_root)
    running, latest_success_id, latest_success_name = _database_protections(engine)

    keep_reasons: dict[str, list[str]] = {}

    def keep(backup_set: _BackupSet, reason: str) -> None:
        keep_reasons.setdefault(backup_set.name, []).append(reason)

    if backup_sets:
        newest = max(backup_sets, key=lambda item: (item.created_at, item.name))
        keep(newest, "newest_overall")

    for backup_set in backup_sets:
        if str(backup_set.run_id) in running or backup_set.name in running:
            keep(backup_set, "running_backup")
        if str(backup_set.run_id) == latest_success_id or backup_set.name == latest_success_name:
            keep(backup_set, "latest_successful_backup")

    daily_dates = sorted({item.created_at.date() for item in backup_sets}, reverse=True)[:7]
    for day in daily_dates:
        newest_for_day = max(
            (item for item in backup_sets if item.created_at.date() == day),
            key=lambda item: (item.created_at, item.name),
        )
        keep(newest_for_day, "daily_retention")

    iso_weeks = sorted(
        {
            (item.created_at.isocalendar().year, item.created_at.isocalendar().week)
            for item in backup_sets
        },
        reverse=True,
    )[:4]
    for iso_year, iso_week in iso_weeks:
        newest_for_week = max(
            (
                item
                for item in backup_sets
                if (item.created_at.isocalendar().year, item.created_at.isocalendar().week)
                == (iso_year, iso_week)
            ),
            key=lambda item: (item.created_at, item.name),
        )
        keep(newest_for_week, "weekly_retention")

    ordered_sets = sorted(backup_sets, key=lambda item: (item.created_at, item.name))
    keep_sets = [item for item in ordered_sets if item.name in keep_reasons]
    delete_sets = [item for item in ordered_sets if item.name not in keep_reasons]
    keep_items = [_item(item, ",".join(keep_reasons[item.name])) for item in keep_sets]
    delete_items = [
        {**_item(item, "outside_retention_windows"), "result": "would_delete"}
        for item in delete_sets
    ]
    result: dict[str, object] = {
        "mode": "apply" if apply else "dry_run",
        "keep": keep_items,
        "delete": delete_items,
        "totals": {
            "keep_sets": len(keep_sets),
            "keep_bytes": sum(item.size_bytes for item in keep_sets),
            "delete_sets": len(delete_sets),
            "delete_bytes": sum(item.size_bytes for item in delete_sets),
            "deleted_sets": 0,
            "deleted_bytes": 0,
            "failed_sets": 0,
            "remaining_sets": 0,
            "remaining_bytes": 0,
        },
    }
    if not apply:
        return result

    failures: list[dict[str, str]] = []
    deleted_names: set[str] = set()
    for backup_set, item in zip(delete_sets, delete_items, strict=True):
        error_code: str | None = None
        if not _safe_to_remove(backup_root, backup_set):
            error_code = "set_changed_or_unsafe"
        else:
            try:
                shutil.rmtree(backup_set.path)
                _fsync_parent(backup_root)
                if _path_exists_without_following(backup_set.path):
                    error_code = "set_remains_after_delete"
                else:
                    deleted_names.add(backup_set.name)
            except OSError:
                error_code = "delete_or_fsync_failed"
        if error_code is None:
            item["result"] = "deleted"
        else:
            item["result"] = "failed"
            item["error_code"] = error_code
            failures.append({"set_name": backup_set.name, "error_code": error_code})

    remaining_sets: list[str] = []
    remaining_bytes = 0
    remaining_bytes_unknown = 0
    for backup_set in delete_sets:
        if not _path_exists_without_following(backup_set.path):
            continue
        remaining_sets.append(backup_set.name)
        size_bytes = _tree_size(backup_set.path)
        if size_bytes is None:
            remaining_bytes_unknown += 1
        else:
            remaining_bytes += size_bytes
    totals = cast(dict[str, object], result["totals"])
    totals.update(
        {
            "deleted_sets": len(deleted_names),
            "deleted_bytes": sum(
                item.size_bytes for item in delete_sets if item.name in deleted_names
            ),
            "failed_sets": len(failures),
            "remaining_sets": len(remaining_sets),
            "remaining_bytes": remaining_bytes,
            "remaining_bytes_unknown": remaining_bytes_unknown,
        }
    )
    result["state"] = "partial_failure" if failures else "applied"
    result["remaining"] = remaining_sets
    result["failures"] = failures
    return result
