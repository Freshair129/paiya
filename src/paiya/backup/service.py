"""Create, verify and restore PostgreSQL 17 backup sets."""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter
from typing import Any, cast
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, insert, select, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response

from paiya.backup.errors import BackupError
from paiya.logging import emit_event, safe_token
from paiya.purge.errors import PurgeError
from paiya.purge.service import (
    lock_backup_media_copy_exclusive_for_session,
    replay_purge_log,
    unlock_backup_media_copy_exclusive_for_session,
)
from paiya.store.media import (
    lock_media_asset_for_session,
    media_path,
    unlock_media_asset_for_session,
)
from paiya.store.tables import (
    backup_runs,
    evidence,
    groups,
    media_assets,
    source_grants,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SETTING_INTEGER_KEYS = (
    "PAIYA_TREND_WINDOW_DAYS",
    "PAIYA_TREND_MIN_OFFERS",
    "PAIYA_TREND_MIN_SELLERS",
    "PAIYA_TREND_FLAT_BAND_PCT",
)
_SETTING_BOOLEAN_KEYS = ("PAIYA_ENRICH_ENABLED",)
_SETTING_ALLOWLIST = frozenset((*_SETTING_INTEGER_KEYS, *_SETTING_BOOLEAN_KEYS))
_MANIFEST_KEYS = {
    "format_version",
    "backup_run_id",
    "created_at",
    "alembic_revision",
    "app_version",
    "db",
    "media",
    "settings",
    "same_device_allowed",
}
_DB_MANIFEST_KEYS = {"file", "sha256", "bytes"}
_MEDIA_MANIFEST_KEYS = {"sha256", "bytes"}
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_CHUNK_BYTES = 1024 * 1024


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _is_path_within(root: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath((str(root), str(candidate)))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(root))


def _repository_roots() -> list[Path]:
    roots: list[Path] = []
    for start in (Path.cwd().resolve(), Path(__file__).resolve()):
        for parent in (start, *start.parents):
            if (parent / ".git").exists() and parent not in roots:
                roots.append(parent)
    if _REPOSITORY_ROOT not in roots and (_REPOSITORY_ROOT / ".git").exists():
        roots.append(_REPOSITORY_ROOT)
    return roots


def _nearest_existing_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise BackupError("backup_destination_invalid", "The backup destination is invalid")
        candidate = parent
    return candidate


def _backup_configuration(*, create_directory: bool = True) -> tuple[Path, Path, Path | None, bool]:
    raw_backup_root = os.environ.get("PAIYA_BACKUP_DIR")
    raw_media_root = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
    raw_log_path = os.environ.get("PAIYA_PURGE_LOG_PATH")
    if not raw_backup_root:
        raise BackupError("backup_dir_required", "PAIYA_BACKUP_DIR must be set")
    if not raw_media_root:
        raise BackupError("media_root_invalid", "PAIYA_FIXTURE_MEDIA_ROOT must be set")
    try:
        backup_root = Path(raw_backup_root).expanduser().resolve()
        media_root = Path(raw_media_root).expanduser().resolve(strict=True)
        purge_log = Path(raw_log_path).expanduser().resolve() if raw_log_path else None
    except (OSError, RuntimeError) as error:
        raise BackupError(
            "backup_destination_invalid", "A configured backup path is invalid"
        ) from error
    if not media_root.is_dir():
        raise BackupError("media_root_invalid", "PAIYA_FIXTURE_MEDIA_ROOT must be a directory")

    forbidden_roots = [media_root]
    if purge_log is not None:
        forbidden_roots.append(purge_log.parent)
    forbidden_roots.extend(_repository_roots())
    if any(_is_path_within(root, backup_root) for root in forbidden_roots):
        raise BackupError(
            "backup_destination_not_permitted",
            "PAIYA_BACKUP_DIR must be outside media, purge-log and repository directories",
        )

    allow_same_device = os.environ.get("PAIYA_BACKUP_ALLOW_SAME_DEVICE") == "1"
    try:
        media_device = media_root.stat().st_dev
        destination_device = _nearest_existing_path(backup_root).stat().st_dev
    except OSError as error:
        raise BackupError(
            "backup_destination_invalid", "The backup destination is unavailable"
        ) from error
    if media_device == destination_device and not allow_same_device:
        raise BackupError(
            "backup_same_device",
            "The backup destination must be on a different device from the media root",
        )

    try:
        if create_directory:
            backup_root.mkdir(parents=True, exist_ok=True)
        if backup_root.exists() and not backup_root.is_dir():
            raise BackupError("backup_destination_invalid", "PAIYA_BACKUP_DIR must be a directory")
        if (
            backup_root.exists()
            and media_device == backup_root.stat().st_dev
            and not allow_same_device
        ):
            raise BackupError(
                "backup_same_device",
                "The backup destination must be on a different device from the media root",
            )
    except BackupError:
        raise
    except OSError as error:
        raise BackupError(
            "backup_destination_invalid", "The backup destination is unavailable"
        ) from error
    return backup_root, media_root, purge_log, allow_same_device


def _manifest_settings() -> dict[str, object]:
    result: dict[str, object] = {}
    for key in _SETTING_INTEGER_KEYS:
        raw_value = os.environ.get(key)
        if raw_value is None:
            continue
        try:
            value = int(raw_value)
        except ValueError as error:
            raise BackupError(
                "backup_setting_invalid", f"{key} must be a positive integer"
            ) from error
        if value <= 0:
            raise BackupError("backup_setting_invalid", f"{key} must be a positive integer")
        result[key] = value
    for key in _SETTING_BOOLEAN_KEYS:
        raw_value = os.environ.get(key)
        if raw_value is None:
            continue
        normalized = raw_value.strip().casefold()
        if normalized not in {"true", "false"}:
            raise BackupError("backup_setting_invalid", f"{key} must be true or false")
        result[key] = normalized == "true"
    return result


def _app_version() -> str:
    try:
        return version("paiya")
    except PackageNotFoundError:
        return "0.1.0"


def _new_backup_run(engine: Engine, run_id: UUID, started_at: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(backup_runs).values(id=run_id, started_at=started_at, state="running")
        )


def _complete_backup_run(
    engine: Engine,
    run_id: UUID,
    *,
    state: str,
    error_code: str | None = None,
    db_bytes: int | None = None,
    media_count: int | None = None,
    media_bytes: int | None = None,
    set_name: str | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(backup_runs)
            .where(backup_runs.c.id == run_id)
            .values(
                completed_at=datetime.now(UTC),
                state=state,
                error_code=error_code,
                db_bytes=db_bytes,
                media_count=media_count,
                media_bytes=media_bytes,
                set_name=set_name,
            )
        )


def _cleanup_owned_path(path: Path | None) -> None:
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


def _pg_command(variable: str, default: str) -> list[str]:
    raw_value = os.environ.get(variable)
    if raw_value is None:
        return [default]
    try:
        command_parts = shlex.split(raw_value, posix=True)
    except ValueError as error:
        raise BackupError(
            "pg_client_command_invalid", f"{variable} is not a valid command"
        ) from error
    if not command_parts:
        raise BackupError("pg_client_command_invalid", f"{variable} is not a valid command")
    return command_parts


def _libpq_url(database_url: URL) -> str:
    if database_url.get_backend_name() != "postgresql":
        raise BackupError("database_url_invalid", "Backup commands require PostgreSQL")
    safe_query = {
        key: value
        for key, value in database_url.query.items()
        if not any(
            secret in key.casefold()
            for secret in ("password", "passwd", "passfile", "secret", "token")
        )
    }
    clean_url = database_url.set(drivername="postgresql", password=None, query=safe_query)
    return clean_url.render_as_string(hide_password=False)


def _client_environment(database_url: URL) -> dict[str, str]:
    environment = os.environ.copy()
    if database_url.password is not None:
        environment["PGPASSWORD"] = database_url.password
    return environment


def _check_pg_client(
    command_parts: Sequence[str], variable: str, environment: Mapping[str, str]
) -> None:
    try:
        completed = subprocess.run(
            [*command_parts, "--version"],
            check=False,
            capture_output=True,
            env=dict(environment),
        )
    except OSError as error:
        raise BackupError("pg_client_unavailable", f"{variable} could not be started") from error
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    match = re.search(r"PostgreSQL\)\s+(\d+)(?:\.|\s)", output)
    if completed.returncode != 0 or match is None:
        raise BackupError("pg_client_unavailable", f"{variable} did not report its version")
    if match.group(1) != "17":
        raise BackupError("pg_client_version", f"{variable} must be PostgreSQL 17")


def _run_pg_dump(database_url: URL, snapshot_id: str, destination: Path) -> int:
    command_parts = _pg_command("PAIYA_PG_DUMP", "pg_dump")
    environment = _client_environment(database_url)
    _check_pg_client(command_parts, "PAIYA_PG_DUMP", environment)
    arguments = [
        *command_parts,
        "--format=custom",
        f"--snapshot={snapshot_id}",
        "--dbname",
        _libpq_url(database_url),
    ]
    try:
        with destination.open("wb") as stream:
            completed = subprocess.run(
                arguments,
                check=False,
                stdout=stream,
                stderr=subprocess.PIPE,
                env=environment,
            )
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise BackupError("pg_client_unavailable", "pg_dump could not complete") from error
    if completed.returncode != 0:
        raise BackupError(
            "backup_tool_failed",
            "pg_dump failed while writing the snapshot",
            {"exit_code": completed.returncode},
        )
    return destination.stat().st_size


def _run_pg_restore(database_url: URL, dump_file: Path) -> None:
    command_parts = _pg_command("PAIYA_PG_RESTORE", "pg_restore")
    environment = _client_environment(database_url)
    _check_pg_client(command_parts, "PAIYA_PG_RESTORE", environment)
    arguments = [
        *command_parts,
        "--exit-on-error",
        "--single-transaction",
        "--dbname",
        _libpq_url(database_url),
    ]
    try:
        with dump_file.open("rb") as stream:
            completed = subprocess.run(
                arguments,
                check=False,
                stdin=stream,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=environment,
            )
    except OSError as error:
        raise BackupError("pg_client_unavailable", "pg_restore could not complete") from error
    if completed.returncode != 0:
        raise BackupError(
            "pg_restore_failed",
            "pg_restore could not restore the verified database snapshot",
            {"exit_code": completed.returncode},
        )


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _copy_asset_contents(
    source_path: Path,
    destination_path: Path,
    expected_digest: str,
    expected_bytes: int,
) -> None:
    digest = hashlib.sha256()
    size = 0
    try:
        with source_path.open("rb") as source, destination_path.open("xb") as destination:
            while chunk := source.read(_CHUNK_BYTES):
                destination.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            destination.flush()
            os.fsync(destination.fileno())
    except FileNotFoundError as error:
        raise BackupError("media_missing", "A referenced media file is missing") from error
    except OSError as error:
        raise BackupError("backup_io_error", "A media file could not be copied") from error
    if size != expected_bytes or digest.hexdigest() != expected_digest:
        raise BackupError("media_hash_mismatch", "A copied media file failed SHA-256 verification")


def _copy_locked_asset(
    connection: Connection,
    media_root: Path,
    set_root: Path,
    digest: str,
    mime: str,
    size: int,
) -> None:
    lock_media_asset_for_session(connection, digest)
    try:
        source_path = media_path(media_root, digest, mime)
        try:
            resolved_source = source_path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise BackupError("media_missing", "A referenced media file is missing") from error
        if (
            source_path.is_symlink()
            or not source_path.is_file()
            or not _is_path_within(media_root, resolved_source)
        ):
            raise BackupError("media_missing", "A referenced media file is missing")
        destination = set_root / "media" / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_asset_contents(source_path, destination, digest, size)
    finally:
        unlock_media_asset_for_session(connection, digest)


def _write_manifest(path: Path, manifest: Mapping[str, object]) -> None:
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("xb") as stream:
        stream.write((encoded + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _exclusive_media_copy_lock(connection: Connection) -> Iterator[None]:
    lock_backup_media_copy_exclusive_for_session(connection)
    try:
        connection.commit()
        yield
    finally:
        if connection.in_transaction():
            connection.rollback()
        unlock_backup_media_copy_exclusive_for_session(connection)
        connection.commit()


def create_backup(engine: Engine) -> dict[str, object]:
    """Export one consistent database and media snapshot into an atomic set."""
    run_id = uuid4()
    started_at = datetime.now(UTC)
    started_clock = perf_counter()
    _new_backup_run(engine, run_id, started_at)
    set_name = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{run_id}"
    temporary_set: Path | None = None
    published_set: Path | None = None
    try:
        settings = _manifest_settings()
        backup_root, media_root, _purge_log, same_device_allowed = _backup_configuration()
        temporary_set = Path(tempfile.mkdtemp(prefix=f".{set_name}.tmp-", dir=backup_root))
        dump_path = temporary_set / "db.dump"
        with engine.connect() as connection, _exclusive_media_copy_lock(connection):
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                snapshot_id = cast(
                    str, connection.execute(text("SELECT pg_export_snapshot()")).scalar_one()
                )
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                if not isinstance(revision, str) or not revision:
                    raise BackupError(
                        "database_revision_missing", "The database revision is unavailable"
                    )
                media_rows = (
                    connection.execute(
                        select(
                            media_assets.c.sha256, media_assets.c.mime, media_assets.c.bytes
                        ).order_by(media_assets.c.sha256)
                    )
                    .mappings()
                    .all()
                )
                db_bytes = _run_pg_dump(engine.url, snapshot_id, dump_path)
                media_manifest: list[dict[str, object]] = []
                media_bytes = 0
                for row in media_rows:
                    digest = cast(str, row["sha256"])
                    mime = cast(str, row["mime"])
                    size = cast(int, row["bytes"])
                    if not _SHA256.fullmatch(digest) or size < 0:
                        raise BackupError(
                            "database_media_invalid", "The media inventory is invalid"
                        )
                    _copy_locked_asset(connection, media_root, temporary_set, digest, mime, size)
                    media_manifest.append({"sha256": digest, "bytes": size})
                    media_bytes += size
                db_digest, actual_db_bytes = _sha256_file(dump_path)
                if actual_db_bytes != db_bytes:
                    raise BackupError(
                        "backup_hash_mismatch", "The database dump changed while writing"
                    )
                manifest: dict[str, object] = {
                    "format_version": 1,
                    "backup_run_id": str(run_id),
                    "created_at": _iso_utc(started_at),
                    "alembic_revision": revision,
                    "app_version": _app_version(),
                    "db": {"file": "db.dump", "sha256": db_digest, "bytes": db_bytes},
                    "media": media_manifest,
                    "settings": settings,
                    "same_device_allowed": same_device_allowed,
                }
                _write_manifest(temporary_set / "manifest.json", manifest)

        candidate_set = backup_root / set_name
        if candidate_set.exists():
            raise BackupError(
                "backup_set_collision", "A backup set with this run ID already exists"
            )
        os.replace(temporary_set, candidate_set)
        temporary_set = None
        published_set = candidate_set
        _complete_backup_run(
            engine,
            run_id,
            state="succeeded",
            db_bytes=db_bytes,
            media_count=len(media_manifest),
            media_bytes=media_bytes,
            set_name=set_name,
        )
        emit_event(
            "backup_run_finished",
            backup_run_id=str(run_id),
            result="succeeded",
            reason=None,
            duration_ms=max(0, int((perf_counter() - started_clock) * 1000)),
        )
        return {
            "state": "succeeded",
            "backup_run_id": str(run_id),
            "set_name": set_name,
            "db_bytes": db_bytes,
            "media_count": len(media_manifest),
            "media_bytes": media_bytes,
            "same_device_allowed": same_device_allowed,
        }
    except BackupError as error:
        _cleanup_owned_path(temporary_set)
        _cleanup_owned_path(published_set)
        try:
            _complete_backup_run(engine, run_id, state="failed", error_code=error.code)
        except SQLAlchemyError:
            pass
        emit_event(
            "backup_run_finished",
            backup_run_id=str(run_id),
            result="failed",
            reason=safe_token(error.code, "backup_failed"),
            duration_ms=max(0, int((perf_counter() - started_clock) * 1000)),
        )
        raise
    except (OSError, SQLAlchemyError, ValueError) as error:
        _cleanup_owned_path(temporary_set)
        _cleanup_owned_path(published_set)
        try:
            _complete_backup_run(engine, run_id, state="failed", error_code="backup_failed")
        except SQLAlchemyError:
            pass
        emit_event(
            "backup_run_finished",
            backup_run_id=str(run_id),
            result="failed",
            reason="backup_failed",
            duration_ms=max(0, int((perf_counter() - started_clock) * 1000)),
        )
        raise BackupError("backup_failed", "The backup run did not complete") from error


def _manifest_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_manifest_settings(value: object) -> bool:
    if not isinstance(value, dict) or not set(value).issubset(_SETTING_ALLOWLIST):
        return False
    for key, setting in value.items():
        if key in _SETTING_INTEGER_KEYS and (
            not isinstance(setting, int) or isinstance(setting, bool) or setting <= 0
        ):
            return False
        if key in _SETTING_BOOLEAN_KEYS and not isinstance(setting, bool):
            return False
    return True


def _valid_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)


def _load_verified_set(set_path: Path) -> tuple[dict[str, Any], dict[str, object]]:
    try:
        resolved_set = set_path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BackupError(
            "backup_set_not_found", "The backup set directory is unavailable"
        ) from error
    if not resolved_set.is_dir():
        raise BackupError("backup_set_invalid", "The backup set must be a directory")
    manifest_path = resolved_set / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupError(
            "backup_manifest_invalid",
            "The backup manifest is missing or is not a regular file",
            {"missing_files": ["manifest.json"], "extra_files": []},
        )
    try:
        raw_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BackupError(
            "backup_manifest_invalid", "The backup manifest could not be parsed"
        ) from error
    if not isinstance(raw_manifest, dict) or set(raw_manifest) != _MANIFEST_KEYS:
        raise BackupError("backup_manifest_invalid", "The backup manifest fields are invalid")

    run_id = raw_manifest.get("backup_run_id")
    if not isinstance(run_id, str):
        raise BackupError("backup_manifest_invalid", "The backup manifest values are invalid")
    try:
        parsed_id = UUID(run_id)
    except ValueError:
        parsed_id = None
    if (
        raw_manifest.get("format_version") != 1
        or isinstance(raw_manifest.get("format_version"), bool)
        or parsed_id is None
        or str(parsed_id) != run_id.casefold()
        or not _valid_utc_timestamp(raw_manifest.get("created_at"))
        or not isinstance(raw_manifest.get("alembic_revision"), str)
        or not raw_manifest.get("alembic_revision")
        or not isinstance(raw_manifest.get("app_version"), str)
        or not raw_manifest.get("app_version")
        or not isinstance(raw_manifest.get("same_device_allowed"), bool)
        or not _validate_manifest_settings(raw_manifest.get("settings"))
    ):
        raise BackupError("backup_manifest_invalid", "The backup manifest values are invalid")

    db_info = raw_manifest.get("db")
    media_info = raw_manifest.get("media")
    if not isinstance(db_info, dict) or set(db_info) != _DB_MANIFEST_KEYS:
        raise BackupError("backup_manifest_invalid", "The database manifest entry is invalid")
    if (
        db_info.get("file") != "db.dump"
        or not isinstance(db_info.get("sha256"), str)
        or _SHA256.fullmatch(cast(str, db_info.get("sha256"))) is None
        or not _manifest_integer(db_info.get("bytes"))
        or not isinstance(media_info, list)
    ):
        raise BackupError("backup_manifest_invalid", "The database manifest values are invalid")

    seen_media: set[str] = set()
    expected_files = {"manifest.json", "db.dump"}
    for item in media_info:
        if not isinstance(item, dict) or set(item) != _MEDIA_MANIFEST_KEYS:
            raise BackupError("backup_manifest_invalid", "A media manifest entry is invalid")
        digest = item.get("sha256")
        if (
            not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or digest in seen_media
            or not _manifest_integer(item.get("bytes"))
        ):
            raise BackupError("backup_manifest_invalid", "A media manifest value is invalid")
        seen_media.add(digest)
        expected_files.add(f"media/{digest[:2]}/{digest}")

    actual_files: set[str] = set()
    try:
        for candidate in resolved_set.rglob("*"):
            if candidate.is_symlink() or candidate.is_file():
                actual_files.add(candidate.relative_to(resolved_set).as_posix())
    except OSError as error:
        raise BackupError(
            "backup_set_unreadable", "The backup set files could not be listed"
        ) from error
    missing_files = sorted(expected_files - actual_files)
    extra_files = sorted(actual_files - expected_files)
    if missing_files or extra_files:
        raise BackupError(
            "backup_files_mismatch",
            "The backup set has files missing from or extra to its manifest",
            {"missing_files": missing_files, "extra_files": extra_files},
        )

    try:
        db_digest, db_bytes = _sha256_file(resolved_set / "db.dump")
        if db_digest != db_info["sha256"] or db_bytes != db_info["bytes"]:
            raise BackupError(
                "backup_hash_mismatch",
                "The database dump failed SHA-256 verification",
                {"file": "db.dump"},
            )
        for item in media_info:
            digest = cast(str, item["sha256"])
            media_file = resolved_set / "media" / digest[:2] / digest
            if media_file.is_symlink() or not media_file.is_file():
                raise BackupError("backup_files_mismatch", "A media file is not a regular file")
            actual_digest, actual_bytes = _sha256_file(media_file)
            if actual_digest != digest or actual_bytes != item["bytes"]:
                raise BackupError(
                    "backup_hash_mismatch",
                    "A media file failed SHA-256 verification",
                    {"file": f"media/{digest[:2]}/{digest}"},
                )
    except BackupError:
        raise
    except OSError as error:
        raise BackupError("backup_set_unreadable", "The backup set could not be read") from error
    return cast(dict[str, Any], raw_manifest), {
        "verified": True,
        "backup_run_id": run_id,
        "db_bytes": db_bytes,
        "media_count": len(media_info),
        "media_bytes": sum(cast(int, item["bytes"]) for item in media_info),
        "missing_files": [],
        "extra_files": [],
    }


def verify_backup(set_path: Path) -> dict[str, object]:
    """Validate a backup manifest, exact file inventory and all content hashes."""
    _manifest, result = _load_verified_set(Path(set_path))
    return result


def _database_identity(connection: Connection) -> tuple[int, datetime]:
    row = connection.execute(
        text(
            "SELECT d.oid, pg_postmaster_start_time() "
            "FROM pg_database AS d WHERE d.datname = current_database()"
        )
    ).one()
    return cast(int, row[0]), cast(datetime, row[1])


def _target_directory_is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        return next(path.iterdir(), None) is None
    except OSError as error:
        raise BackupError(
            "target_media_unavailable", "The target media directory cannot be read"
        ) from error


def _target_database_has_tables(engine: Engine) -> bool:
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public')")
            ).scalar_one()
            is True
        )


def _copy_restored_media(
    set_path: Path, target_root: Path, engine: Engine, manifest: Mapping[str, Any]
) -> None:
    manifest_media = {
        cast(str, item["sha256"]): cast(int, item["bytes"])
        for item in cast(list[dict[str, object]], manifest["media"])
    }
    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(media_assets.c.sha256, media_assets.c.mime, media_assets.c.bytes).order_by(
                    media_assets.c.sha256
                )
            )
            .mappings()
            .all()
        )
    database_media = {cast(str, row["sha256"]): cast(int, row["bytes"]) for row in rows}
    if database_media != manifest_media:
        raise BackupError(
            "backup_manifest_mismatch",
            "The media manifest does not match the restored database",
        )
    for row in rows:
        digest = cast(str, row["sha256"])
        mime = cast(str, row["mime"])
        size = cast(int, row["bytes"])
        source = set_path / "media" / digest[:2] / digest
        destination = media_path(target_root, digest, mime)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_asset_contents(source, destination, digest, size)


@contextmanager
def _temporary_environment(name: str, value: str) -> Iterator[None]:
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _alembic_config() -> Config:
    config_path = _REPOSITORY_ROOT / "alembic.ini"
    if not config_path.is_file():
        raise BackupError("migration_configuration_missing", "Alembic configuration is unavailable")
    return Config(str(config_path))


def _upgrade_restored_database(engine: Engine, database_url: str, manifest_revision: str) -> bool:
    with engine.connect() as connection:
        current_revision = connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one_or_none()
    if current_revision != manifest_revision:
        raise BackupError(
            "backup_manifest_mismatch",
            "The restored Alembic revision does not match the manifest",
        )
    config = _alembic_config()
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    revisions = [revision.revision for revision in script.walk_revisions()]
    if len(heads) != 1 or manifest_revision not in revisions or heads[0] not in revisions:
        raise BackupError(
            "migration_revision_unsupported",
            "The backup migration revision is not supported by this application",
        )
    if revisions.index(manifest_revision) < revisions.index(heads[0]):
        raise BackupError(
            "migration_revision_unsupported",
            "The backup migration revision is newer than this application",
        )
    if manifest_revision == heads[0]:
        return False
    try:
        with _temporary_environment("PAIYA_DATABASE_URL", database_url):
            command.upgrade(config, "head")
    except (OSError, SQLAlchemyError, RuntimeError, ValueError) as error:
        raise BackupError(
            "migration_upgrade_failed", "Alembic could not upgrade the restored database"
        ) from error
    with engine.connect() as connection:
        upgraded_revision = connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one_or_none()
    if upgraded_revision != heads[0]:
        raise BackupError(
            "migration_upgrade_failed", "The restored database did not reach Alembic head"
        )
    return True


def _check_restored_orphans(engine: Engine, media_root: Path) -> dict[str, int]:
    expected_files: set[str] = set()
    rows_without_files = 0
    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(media_assets.c.sha256, media_assets.c.mime, media_assets.c.bytes)
            )
            .mappings()
            .all()
        )
    for row in rows:
        digest = cast(str, row["sha256"])
        mime = cast(str, row["mime"])
        size = cast(int, row["bytes"])
        try:
            file_path = media_path(media_root, digest, mime)
        except ValueError as error:
            raise BackupError(
                "media_hash_mismatch", "The restored media inventory is invalid"
            ) from error
        expected_files.add(file_path.relative_to(media_root).as_posix())
        if file_path.is_symlink() or not file_path.is_file():
            rows_without_files += 1
            continue
        actual_digest, actual_size = _sha256_file(file_path)
        if actual_digest != digest or actual_size != size:
            raise BackupError(
                "media_hash_mismatch", "A restored media file failed SHA-256 verification"
            )

    actual_files: set[str] = set()
    try:
        for candidate in media_root.rglob("*"):
            if candidate.is_symlink() or candidate.is_file():
                actual_files.add(candidate.relative_to(media_root).as_posix())
    except OSError as error:
        raise BackupError(
            "media_storage_unavailable", "The restored media directory cannot be read"
        ) from error
    files_without_rows = len(actual_files - expected_files)
    result = {"rows_without_files": rows_without_files, "files_without_rows": files_without_rows}
    if rows_without_files or files_without_rows:
        raise BackupError(
            "media_orphans_found", "The restored media directory has orphan files", result
        )
    return result


def _api_sample(engine: Engine, media_root: Path) -> dict[str, int]:
    from paiya.api.evidence import get_evidence_media, get_post_timeline
    from paiya.read.service import bangkok_now, read_posts
    from paiya.store.fixture_scope import FixtureScope

    now = datetime.now(UTC)
    posts_checked = 0
    timelines_checked = 0
    images_checked = 0
    with engine.connect() as connection:
        grant_rows = connection.execute(
            select(groups.c.group_key, groups.c.id, source_grants.c.id)
            .select_from(groups.join(source_grants, source_grants.c.group_id == groups.c.id))
            .where(
                groups.c.source == "fixture",
                source_grants.c.source_kind == "fixture",
                source_grants.c.revoked_at.is_(None),
                source_grants.c.valid_from <= now,
                (source_grants.c.valid_until.is_(None) | (source_grants.c.valid_until > now)),
                source_grants.c.scopes.contains(["read"]),
            )
            .order_by(groups.c.group_key, source_grants.c.valid_from.desc(), source_grants.c.id)
        ).all()
        contexts: dict[str, FixtureScope] = {}
        for group_key, group_id, grant_id in grant_rows:
            key = cast(str, group_key)
            contexts.setdefault(
                key,
                FixtureScope(
                    group_id=cast(UUID, group_id),
                    grant_id=cast(UUID, grant_id),
                    group_key=key,
                ),
            )
        with _temporary_environment("PAIYA_FIXTURE_MEDIA_ROOT", str(media_root)):
            for context in contexts.values():
                if posts_checked >= 5:
                    break
                remaining = 5 - posts_checked
                cards = cast(
                    list[dict[str, Any]],
                    read_posts(
                        connection,
                        context,
                        limit=remaining,
                        now=bangkok_now(),
                    )["posts"],
                )
                for card in cards:
                    post_id = cast(UUID, card["post_id"])
                    timeline = get_post_timeline(
                        post_id, context, connection, limit=50, cursor=None
                    )
                    if not isinstance(timeline, dict) or not isinstance(
                        timeline.get("events"), list
                    ):
                        raise BackupError(
                            "api_sample_failed", "The restored API timeline check failed"
                        )
                    timelines_checked += 1
                    media_id = connection.execute(
                        select(evidence.c.id)
                        .where(
                            evidence.c.post_id == post_id,
                            evidence.c.grant_id == context.grant_id,
                            evidence.c.media_asset_id.is_not(None),
                            evidence.c.media_state == "captured",
                        )
                        .order_by(evidence.c.id)
                        .limit(1)
                    ).scalar_one_or_none()
                    if media_id is not None:
                        response = get_evidence_media(media_id, context, connection)
                        if (
                            not isinstance(response, Response)
                            or response.status_code != 200
                            or not response.body
                        ):
                            raise BackupError(
                                "api_sample_failed", "The restored API media check failed"
                            )
                        images_checked += 1
                    posts_checked += 1
                    if posts_checked >= 5:
                        break
    return {
        "posts_checked": posts_checked,
        "timelines_checked": timelines_checked,
        "images_checked": images_checked,
    }


def restore_backup(
    live_engine: Engine,
    *,
    set_path: Path,
    target_database_url: str,
    target_media_root: Path,
) -> dict[str, object]:
    """Restore a verified set only into a new, empty and non-live target."""
    manifest, verification = _load_verified_set(Path(set_path))
    try:
        target_url = make_url(target_database_url)
        if target_url.get_backend_name() != "postgresql":
            raise ValueError("target URL is not PostgreSQL")
    except (TypeError, ValueError) as error:
        raise BackupError(
            "target_database_invalid", "The target database URL is invalid"
        ) from error

    raw_live_media_root = os.environ.get("PAIYA_FIXTURE_MEDIA_ROOT")
    if not raw_live_media_root:
        raise BackupError("media_root_invalid", "PAIYA_FIXTURE_MEDIA_ROOT must be set")
    try:
        live_media_root = Path(raw_live_media_root).expanduser().resolve(strict=True)
        target_root = Path(target_media_root).expanduser().resolve()
    except (OSError, RuntimeError) as error:
        raise BackupError(
            "target_media_unavailable", "A media directory could not be resolved"
        ) from error
    if target_root == live_media_root:
        raise BackupError(
            "target_is_live", "The restore media directory is the live media directory"
        )

    try:
        target_engine = create_engine(target_url, pool_pre_ping=True)
    except (SQLAlchemyError, ValueError) as error:
        raise BackupError(
            "target_database_unavailable", "The target database could not be opened"
        ) from error
    try:
        with live_engine.connect() as live_connection, target_engine.connect() as target_connection:
            if _database_identity(live_connection) == _database_identity(target_connection):
                raise BackupError("target_is_live", "The target database is the live database")
        if _target_database_has_tables(target_engine) or not _target_directory_is_empty(
            target_root
        ):
            raise BackupError("target_not_empty", "The restore targets must be empty")

        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise BackupError(
                "target_media_unavailable", "The target media directory cannot be created"
            ) from error

        dump_path = Path(set_path).expanduser().resolve() / "db.dump"
        current_dump_hash, current_dump_bytes = _sha256_file(dump_path)
        db_info = cast(dict[str, object], manifest["db"])
        if current_dump_hash != db_info["sha256"] or current_dump_bytes != db_info["bytes"]:
            raise BackupError(
                "backup_hash_mismatch", "The database dump changed after verification"
            )

        _run_pg_restore(target_url, dump_path)
        restored_run_id = UUID(cast(str, manifest["backup_run_id"]))
        restored_completed_at = datetime.fromisoformat(
            cast(str, manifest["created_at"]).replace("Z", "+00:00")
        )
        with target_engine.begin() as connection:
            restored_run = connection.execute(
                update(backup_runs)
                .where(backup_runs.c.id == restored_run_id)
                .values(state="succeeded", completed_at=restored_completed_at)
            )
        if restored_run.rowcount != 1:
            raise BackupError(
                "backup_run_missing", "The restored backup run record could not be completed"
            )
        _copy_restored_media(
            Path(set_path).expanduser().resolve(), target_root, target_engine, manifest
        )
        migration_upgraded = _upgrade_restored_database(
            target_engine,
            target_url.render_as_string(hide_password=False),
            cast(str, manifest["alembic_revision"]),
        )

        if not os.environ.get("PAIYA_PURGE_LOG_PATH"):
            raise BackupError(
                "purge_log_path_required", "PAIYA_PURGE_LOG_PATH must be set for restore"
            )
        try:
            with _temporary_environment("PAIYA_FIXTURE_MEDIA_ROOT", str(target_root)):
                replay_result = replay_purge_log(target_engine)
        except PurgeError as error:
            raise BackupError(error.code, error.message) from error
        if replay_result["pending_count"] != 0:
            raise BackupError(
                "purge_replay_incomplete",
                "The current purge log still has pending commands",
                {"pending_count": replay_result["pending_count"]},
            )
        orphan_check = _check_restored_orphans(target_engine, target_root)
        api_sample = _api_sample(target_engine, target_root)
        return {
            "ready": True,
            "backup_run_id": verification["backup_run_id"],
            "target_database": target_url.database,
            "steps": {
                "verify": True,
                "target_check": True,
                "pg_restore": True,
                "media_copy": True,
                "alembic_upgrade": True,
                "purge_replay": True,
                "orphan_check": True,
                "api_sample": True,
            },
            "migration_upgrade_performed": migration_upgraded,
            "purge_replay": replay_result,
            "orphan_check": orphan_check,
            "api_sample": api_sample,
        }
    except BackupError:
        raise
    except (OSError, SQLAlchemyError, RuntimeError, ValueError) as error:
        raise BackupError("restore_failed", "The restore did not complete") from error
    finally:
        target_engine.dispose()


def backup_status(engine: Engine, *, now: datetime | None = None) -> dict[str, object]:
    """Report the latest successful and failed create runs and success age."""
    with engine.connect() as connection:
        success = (
            connection.execute(
                select(backup_runs)
                .where(backup_runs.c.state == "succeeded")
                .order_by(backup_runs.c.completed_at.desc(), backup_runs.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        failure = (
            connection.execute(
                select(backup_runs)
                .where(backup_runs.c.state == "failed")
                .order_by(backup_runs.c.completed_at.desc(), backup_runs.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
    normalized_now = now or datetime.now(UTC)
    last_success: dict[str, object] | None = None
    if success is not None:
        completed_at = cast(datetime, success["completed_at"])
        last_success = {
            "backup_run_id": str(success["id"]),
            "completed_at": _iso_utc(completed_at),
            "age_hours": round(max(0.0, (normalized_now - completed_at).total_seconds() / 3600), 3),
            "set_name": success["set_name"],
            "db_bytes": success["db_bytes"],
            "media_count": success["media_count"],
            "media_bytes": success["media_bytes"],
        }
    last_failure: dict[str, object] | None = None
    if failure is not None:
        last_failure = {
            "backup_run_id": str(failure["id"]),
            "started_at": _iso_utc(cast(datetime, failure["started_at"])),
            "completed_at": _iso_utc(cast(datetime, failure["completed_at"])),
            "error_code": failure["error_code"],
        }
    return {"last_success": last_success, "last_failure": last_failure}
