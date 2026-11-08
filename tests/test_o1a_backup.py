"""Synthetic O1a backup and restore acceptance tests on PostgreSQL 17."""

import asyncio
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from test_a2_ingest import WEBP
from test_a8b_purge import image_document

from paiya import cli
from paiya.backup import service as backup_service
from paiya.backup.errors import BackupError
from paiya.backup.service import backup_status, create_backup, restore_backup, verify_backup
from paiya.grants.service import revoke_grant
from paiya.ingest.service import ingest_fixture
from paiya.purge.service import run_purge
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.media import media_path
from paiya.store.tables import backup_runs, evidence, media_assets, metadata, observations

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DatabasePair:
    source: Engine
    source_url: URL
    target: Engine
    target_url: URL


@pytest.fixture
def database_pair(monkeypatch: pytest.MonkeyPatch) -> Iterator[DatabasePair]:
    raw_url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    if not raw_url:
        pytest.fail("PAIYA_TEST_DATABASE_URL must point to the PostgreSQL 17 test database")
    admin_url = make_url(raw_url)
    if admin_url.database != "paiya_test":
        pytest.fail("O1a tests may create temporary databases only from paiya_test")
    if admin_url.password is not None:
        monkeypatch.setenv("PGPASSWORD", admin_url.password)
    admin = create_engine(admin_url)
    suffix = uuid4().hex
    source_name = f"paiya_o1a_source_{suffix}"
    target_name = f"paiya_o1a_target_{suffix}"
    source_url = admin_url.set(database=source_name)
    target_url = admin_url.set(database=target_name)
    source: Engine | None = None
    target: Engine | None = None
    try:
        with admin.begin() as connection:
            version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
            assert version // 10000 == 17
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{source_name}"')
            connection.exec_driver_sql(f'CREATE DATABASE "{target_name}"')
        source = create_engine(source_url)
        target = create_engine(target_url)
        monkeypatch.setenv("PAIYA_DATABASE_URL", source_url.render_as_string(hide_password=False))
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        yield DatabasePair(source, source_url, target, target_url)
    finally:
        if source is not None:
            source.dispose()
        if target is not None:
            target.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname IN (:source, :target) AND pid <> pg_backend_pid()"
                ),
                {"source": source_name, "target": target_name},
            )
            for database_name in (target_name, source_name):
                connection.exec_driver_sql(
                    f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
                )
        admin.dispose()


@pytest.fixture
def backup_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    media_root = tmp_path / "live-media"
    media_root.mkdir()
    purge_dir = tmp_path / "purge-log"
    purge_dir.mkdir()
    backup_dir = tmp_path / "backup-sets"
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(purge_dir / "purge.jsonl"))
    monkeypatch.setenv("PAIYA_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("PAIYA_BACKUP_ALLOW_SAME_DEVICE", "1")
    return media_root, purge_dir, backup_dir


@pytest.fixture
def pg_tool_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("PAIYA_PG_DUMP") and os.environ.get("PAIYA_PG_RESTORE"):
        return
    proxy = tmp_path / "pg_tool_proxy.py"
    proxy.write_text(
        "import subprocess\n"
        "import sys\n"
        "from sqlalchemy.engine import make_url\n"
        "tool, *args = sys.argv[1:]\n"
        "has_snapshot = any(\n"
        "    arg.startswith('--snapshot=') and len(arg) > 11 for arg in args\n"
        ")\n"
        "if tool == 'pg_dump' and '--version' not in args and not has_snapshot:\n"
        "    raise SystemExit('pg_dump requires an exported snapshot')\n"
        "for index, argument in enumerate(args):\n"
        "    if argument == '--dbname' and index + 1 < len(args):\n"
        "        url = make_url(args[index + 1])\n"
        "        if url.port == 55432:\n"
        "            url = url.set(drivername='postgresql', port=5432)\n"
        "            args[index + 1] = url.render_as_string(hide_password=False)\n"
        "    elif argument.startswith('--dbname='):\n"
        "        url = make_url(argument.split('=', 1)[1])\n"
        "        if url.port == 55432:\n"
        "            url = url.set(drivername='postgresql', port=5432)\n"
        "            args[index] = '--dbname=' + url.render_as_string(hide_password=False)\n"
        "command = ['docker', 'exec', '-i', '-e', 'PGPASSWORD', 'paiya-pg17', tool, *args]\n"
        "completed = subprocess.run(command, check=False)\n"
        "raise SystemExit(completed.returncode)\n",
        encoding="utf-8",
    )
    for variable, tool in (("PAIYA_PG_DUMP", "pg_dump"), ("PAIYA_PG_RESTORE", "pg_restore")):
        if not os.environ.get(variable):
            monkeypatch.setenv(variable, shlex.join([sys.executable, str(proxy), tool]))


def seed_media_post(
    engine: Engine,
    media_root: Path,
    group_key: str,
    *,
    data: bytes = WEBP,
    model: str = "RTX 3060",
) -> tuple[FixtureScope, object, object]:
    scope = seed_fixture_scope(engine, group_key)
    document = image_document(scope, "o1a-post", data)
    document["payload"]["items"][0]["model"] = model
    document["payload"]["items"][0]["price"] = {
        "kind": "ask",
        "unit": "each",
        "amount": "5500",
        "currency": "THB",
    }
    document["payload"]["evidence"][0]["source_span"]["quote"] = f"{model} 5500 เปิดขาย"
    observation_id = ingest_fixture(engine, scope, document, {"image": data}, media_root)
    with engine.connect() as connection:
        post_id = connection.execute(
            select(observations.c.post_id).where(observations.c.id == observation_id)
        ).scalar_one()
        evidence_id = connection.execute(
            select(evidence.c.id).where(evidence.c.observation_id == observation_id)
        ).scalar_one()
    return scope, post_id, evidence_id


def table_row_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        result = {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in metadata.sorted_tables
        }
        result["alembic_version"] = connection.execute(
            text("SELECT count(*) FROM alembic_version")
        ).scalar_one()
    return result


def test_restore_refuses_pending_purge_replay(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, "synthetic-o1a-pending-replay")
    created = create_backup(database_pair.source)
    original_replay = backup_service.replay_purge_log

    def replay_with_pending(engine: Engine) -> dict[str, object]:
        result = original_replay(engine)
        return {**result, "pending_count": 1}

    monkeypatch.setattr(backup_service, "replay_purge_log", replay_with_pending)
    with pytest.raises(BackupError) as failure:
        restore_backup(
            database_pair.source,
            set_path=backup_dir / str(created["set_name"]),
            target_database_url=database_pair.target_url.set(password=None).render_as_string(
                hide_password=False
            ),
            target_media_root=tmp_path / "pending-replay-media",
        )
    assert failure.value.code == "purge_replay_incomplete"


@pytest.mark.parametrize("orphan", ["missing_file", "extra_file"])
def test_restore_refuses_media_orphans_in_both_directions(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    orphan: str,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, "synthetic-o1a-restore-orphan")
    created = create_backup(database_pair.source)
    original_copy = backup_service._copy_restored_media

    def copy_then_introduce_orphan(*args: object) -> None:
        original_copy(*args)
        target_root = args[1]
        assert isinstance(target_root, Path)
        if orphan == "missing_file":
            files = [path for path in target_root.rglob("*") if path.is_file()]
            assert len(files) == 1
            files[0].unlink()
        else:
            (target_root / "synthetic-orphan").write_bytes(b"synthetic-extra")

    monkeypatch.setattr(backup_service, "_copy_restored_media", copy_then_introduce_orphan)
    with pytest.raises(BackupError) as failure:
        restore_backup(
            database_pair.source,
            set_path=backup_dir / str(created["set_name"]),
            target_database_url=database_pair.target_url.set(password=None).render_as_string(
                hide_password=False
            ),
            target_media_root=tmp_path / "orphan-media",
        )
    assert failure.value.code == "media_orphans_found"


def test_backup_status_cli_emits_one_json_result(
    database_pair: DatabasePair,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "get_database_engine", lambda: database_pair.source)
    assert cli.main(["backup", "status"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"ok": True, "result": {"last_success": None, "last_failure": None}}


def test_backup_cli_failure_emits_one_json_error(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    media_root, _, _ = backup_paths
    monkeypatch.setattr(cli, "get_database_engine", lambda: database_pair.source)
    monkeypatch.setenv("PAIYA_BACKUP_DIR", str(media_root / "forbidden"))

    assert cli.main(["backup", "create"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": {
            "code": "backup_destination_not_permitted",
            "message": (
                "PAIYA_BACKUP_DIR must be outside media, purge-log and repository directories"
            ),
        },
    }


def test_backup_round_trip_copies_a_consistent_snapshot_and_checks_api(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, "synthetic-o1a-round-trip")
    before_counts = table_row_counts(database_pair.source)

    monkeypatch.setattr(cli, "get_database_engine", lambda: database_pair.source)
    assert cli.main(["backup", "create"]) == 0
    created = json.loads(capsys.readouterr().out)["result"]
    after_counts = table_row_counts(database_pair.source)
    assert {name: count for name, count in after_counts.items() if name != "backup_runs"} == {
        name: count for name, count in before_counts.items() if name != "backup_runs"
    }
    assert after_counts["backup_runs"] == before_counts["backup_runs"] + 1
    set_path = backup_dir / str(created["set_name"])
    assert created["state"] == "succeeded"
    assert set_path.is_dir()
    assert cli.main(["backup", "verify", "--set", str(set_path)]) == 0
    verified = json.loads(capsys.readouterr().out)["result"]
    assert verified["verified"] is True

    target_media = tmp_path / "restored-media"
    assert (
        cli.main(
            [
                "backup",
                "restore",
                "--set",
                str(set_path),
                "--target-database-url",
                database_pair.target_url.set(password=None).render_as_string(hide_password=False),
                "--target-media-root",
                str(target_media),
            ]
        )
        == 0
    )
    restored = json.loads(capsys.readouterr().out)["result"]
    assert restored["ready"] is True
    assert restored["steps"]["verify"] is True
    assert restored["api_sample"]["timelines_checked"] >= 1
    assert restored["api_sample"]["images_checked"] >= 1
    assert restored["orphan_check"] == {"rows_without_files": 0, "files_without_rows": 0}
    assert table_row_counts(database_pair.target) == after_counts
    manifest = json.loads((set_path / "manifest.json").read_text(encoding="utf-8"))
    for media in manifest["media"]:
        digest = media["sha256"]
        assert (
            hashlib.sha256((set_path / "media" / digest[:2] / digest).read_bytes()).hexdigest()
            == digest
        )
    _assert_restored_media_byte_for_byte(set_path, target_media)


def _assert_restored_media_byte_for_byte(set_path: Path, target_media: Path) -> None:
    """Use a standalone script in the system temp directory to verify restored bytes."""
    script = Path(tempfile.gettempdir()) / f"paiya_o1a_sha256_{uuid4().hex}.py"
    script.write_text(
        "import hashlib, json, sys\n"
        "from pathlib import Path\n"
        "backup, restored = map(Path, sys.argv[1:])\n"
        "manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))\n"
        "files = [path for path in restored.rglob('*') if path.is_file()]\n"
        "for item in manifest['media']:\n"
        "    digest = item['sha256']\n"
        "    archived = backup / 'media' / digest[:2] / digest\n"
        "    matches = [\n"
        "        path for path in files\n"
        "        if hashlib.sha256(path.read_bytes()).hexdigest() == digest\n"
        "    ]\n"
        "    if len(matches) != 1 or matches[0].read_bytes() != archived.read_bytes():\n"
        "        raise SystemExit(f'restored bytes differ for {digest}')\n"
        "print(json.dumps({'verified_media_files': len(manifest['media'])}))\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(set_path), str(target_media)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert json.loads(result.stdout)["verified_media_files"] > 0
    finally:
        script.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("destination", "error_code"),
    [
        ("media", "backup_destination_not_permitted"),
        ("purge", "backup_destination_not_permitted"),
        ("repository", "backup_destination_not_permitted"),
    ],
)
def test_create_refuses_backup_destinations_inside_protected_paths(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    destination: str,
    error_code: str,
) -> None:
    media_root, purge_dir, _backup_dir = backup_paths
    blocked_path = {
        "media": media_root / "backup-sets",
        "purge": purge_dir / "backup-sets",
        "repository": ROOT / "tests" / ".paiya-o1a-backups",
    }[destination]
    monkeypatch.setenv("PAIYA_BACKUP_DIR", str(blocked_path))
    with pytest.raises(BackupError) as failure:
        create_backup(database_pair.source)
    assert failure.value.code == error_code
    assert not blocked_path.exists()


def test_create_refuses_same_device_without_the_explicit_override(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAIYA_BACKUP_ALLOW_SAME_DEVICE", "0")
    with pytest.raises(BackupError) as failure:
        create_backup(database_pair.source)
    assert failure.value.code == "backup_same_device"


@pytest.mark.parametrize(
    ("media_state", "error_code"),
    [("missing", "media_missing"), ("modified", "media_hash_mismatch")],
)
def test_failed_media_copy_is_recorded_and_never_published(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    media_state: str,
    error_code: str,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, f"synthetic-o1a-{media_state}")
    with database_pair.source.connect() as connection:
        digest = connection.execute(select(media_assets.c.sha256)).scalar_one()
    source_file = media_path(media_root, digest, "image/webp")
    if media_state == "missing":
        source_file.unlink()
    else:
        data = bytearray(source_file.read_bytes())
        data[0] ^= 1
        source_file.write_bytes(data)

    with pytest.raises(BackupError) as failure:
        create_backup(database_pair.source)
    assert failure.value.code == error_code
    assert list(backup_dir.iterdir()) == []
    with database_pair.source.connect() as connection:
        row = connection.execute(select(backup_runs.c.state, backup_runs.c.error_code)).one()
    assert row == ("failed", error_code)


@pytest.mark.parametrize("changed_file", ["db", "media"])
def test_verify_rejects_hash_mutations(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    changed_file: str,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, f"synthetic-o1a-tampered-{changed_file}")
    created = create_backup(database_pair.source)
    set_path = backup_dir / str(created["set_name"])
    if changed_file == "db":
        db_file = set_path / "db.dump"
        data = bytearray(db_file.read_bytes())
        data[0] ^= 1
        db_file.write_bytes(data)
    else:
        manifest = json.loads((set_path / "manifest.json").read_text(encoding="utf-8"))
        digest = manifest["media"][0]["sha256"]
        (set_path / "media" / digest[:2] / digest).write_bytes(b"synthetic-corruption")
    with pytest.raises(BackupError) as failure:
        verify_backup(set_path)
    assert failure.value.code == "backup_hash_mismatch"


def test_verify_reports_manifest_files_that_are_missing_and_extra(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, "synthetic-o1a-file-list")
    created = create_backup(database_pair.source)
    set_path = backup_dir / str(created["set_name"])
    manifest = json.loads((set_path / "manifest.json").read_text(encoding="utf-8"))
    digest = manifest["media"][0]["sha256"]
    expected_media = f"media/{digest[:2]}/{digest}"
    (set_path / "media" / digest[:2] / digest).unlink()
    (set_path / "unexpected.txt").write_text("synthetic-extra", encoding="utf-8")
    with pytest.raises(BackupError) as failure:
        verify_backup(set_path)
    assert failure.value.code == "backup_files_mismatch"
    assert failure.value.details == {
        "missing_files": [expected_media],
        "extra_files": ["unexpected.txt"],
    }


@pytest.mark.parametrize("nonempty_target", ["database", "media"])
def test_restore_refuses_nonempty_targets_without_modifying_them(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    tmp_path: Path,
    nonempty_target: str,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, "synthetic-o1a-target-nonempty")
    created = create_backup(database_pair.source)
    target_media = tmp_path / "restore-nonempty-target"
    sentinel = target_media / "keep.txt"
    if nonempty_target == "database":
        with database_pair.target.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE public.already_present (id integer)")
    else:
        target_media.mkdir()
        sentinel.write_text("synthetic-existing-file", encoding="utf-8")
    with pytest.raises(BackupError) as failure:
        restore_backup(
            database_pair.source,
            set_path=backup_dir / str(created["set_name"]),
            target_database_url=database_pair.target_url.set(password=None).render_as_string(
                hide_password=False
            ),
            target_media_root=target_media,
        )
    assert failure.value.code == "target_not_empty"
    if nonempty_target == "database":
        assert not target_media.exists()
        with database_pair.target.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM public.already_present")).scalar_one()
                == 0
            )
    else:
        assert sentinel.read_text(encoding="utf-8") == "synthetic-existing-file"
        with database_pair.target.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
                ).scalar_one()
                == 0
            )


@pytest.mark.parametrize("live_target", ["database", "media"])
def test_restore_refuses_the_live_database_or_media_directory(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    tmp_path: Path,
    live_target: str,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, f"synthetic-o1a-live-{live_target}")
    created = create_backup(database_pair.source)
    target_url = (
        (database_pair.source_url if live_target == "database" else database_pair.target_url)
        .set(password=None)
        .render_as_string(hide_password=False)
    )
    target_media = media_root if live_target == "media" else tmp_path / "live-check-media"
    with pytest.raises(BackupError) as failure:
        restore_backup(
            database_pair.source,
            set_path=backup_dir / str(created["set_name"]),
            target_database_url=target_url,
            target_media_root=target_media,
        )
    assert failure.value.code == "target_is_live"
    if live_target == "database":
        assert not target_media.exists()
    assert table_row_counts(database_pair.source)["observations"] == 1


def test_status_reports_latest_success_age_and_latest_failure(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, "synthetic-o1a-status")
    succeeded = create_backup(database_pair.source)
    monkeypatch.setenv("PAIYA_BACKUP_DIR", str(media_root / "forbidden"))
    with pytest.raises(BackupError) as failure:
        create_backup(database_pair.source)
    assert failure.value.code == "backup_destination_not_permitted"
    status = backup_status(database_pair.source)
    assert status["last_success"]["backup_run_id"] == succeeded["backup_run_id"]
    assert status["last_success"]["age_hours"] >= 0
    assert status["last_success"]["set_name"] == succeeded["set_name"]
    assert status["last_failure"]["error_code"] == "backup_destination_not_permitted"
    assert (backup_dir / str(succeeded["set_name"])).is_dir()


def test_backup_set_uses_an_allow_list_and_contains_no_database_or_purge_secrets(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root, purge_dir, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, "synthetic-o1a-secret-scan")
    monkeypatch.setenv("PAIYA_TREND_WINDOW_DAYS", "7")
    monkeypatch.setenv("PAIYA_ENRICH_ENABLED", "false")
    for name in ("PAIYA_TREND_MIN_OFFERS", "PAIYA_TREND_MIN_SELLERS", "PAIYA_TREND_FLAT_BAND_PCT"):
        monkeypatch.delenv(name, raising=False)
    purge_path = purge_dir / "synthetic-purge-path-secret.jsonl"
    purge_contents = b"synthetic-purge-log-secret-content"
    purge_path.write_bytes(purge_contents)
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(purge_path))
    database_password = make_url(os.environ["PAIYA_DATABASE_URL"]).password
    assert database_password is not None

    created = create_backup(database_pair.source)
    set_path = backup_dir / str(created["set_name"])
    manifest = json.loads((set_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["settings"] == {
        "PAIYA_TREND_WINDOW_DAYS": 7,
        "PAIYA_ENRICH_ENABLED": False,
    }
    assert manifest["same_device_allowed"] is True
    forbidden_values = (
        database_password.encode(),
        str(purge_path).encode(),
        purge_contents,
    )
    files = [path for path in set_path.rglob("*") if path.is_file()]
    if any(secret in path.read_bytes() for path in files for secret in forbidden_values):
        pytest.fail("the backup set contains a database or purge-log secret")


def test_restore_replays_a_post_backup_purge_before_api_mcp_and_stats(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_root, _, backup_dir = backup_paths
    kept_scope, kept_post, _ = seed_media_post(
        database_pair.source,
        media_root,
        "synthetic-o1a-kept",
        model="RTX 3060",
    )
    removed_data = WEBP + b"synthetic-o1a-private-image"
    removed_scope, removed_post, _ = seed_media_post(
        database_pair.source,
        media_root,
        "synthetic-o1a-removed",
        data=removed_data,
        model="RTX 3070",
    )
    removed_digest = hashlib.sha256(removed_data).hexdigest()
    created = create_backup(database_pair.source)
    set_path = backup_dir / str(created["set_name"])

    revoke_grant(database_pair.source, removed_scope.grant_id)
    purge_result = run_purge(database_pair.source, f"grant:{removed_scope.grant_id}")
    assert purge_result["state"] == "succeeded"
    assert not media_path(media_root, removed_digest, "image/webp").exists()

    target_media = tmp_path / "replayed-media"
    restored = restore_backup(
        database_pair.source,
        set_path=set_path,
        target_database_url=database_pair.target_url.set(password=None).render_as_string(
            hide_password=False
        ),
        target_media_root=target_media,
    )
    assert restored["ready"] is True
    assert restored["purge_replay"]["pending_count"] == 0
    assert any(
        item["purge_id"] == purge_result["purge_id"] for item in restored["purge_replay"]["results"]
    )
    assert not media_path(target_media, removed_digest, "image/webp").exists()
    assert media_path(target_media, hashlib.sha256(WEBP).hexdigest(), "image/webp").is_file()
    with database_pair.target.connect() as connection:
        assert (
            connection.execute(
                select(observations.c.id).where(observations.c.post_id == removed_post)
            ).first()
            is None
        )

    from test_a6c_mcp import session

    from paiya.api.app import app
    from paiya.api.evidence import get_db_connection, require_fixture_scope
    from paiya.mcp import server as mcp_server

    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", kept_scope.group_key)
    monkeypatch.setattr(mcp_server, "get_database_engine", lambda: database_pair.target)
    monkeypatch.setattr(
        mcp_server,
        "resolve_read_context",
        lambda _connection, _group_key: kept_scope,
    )

    def target_connection():
        with database_pair.target.connect() as connection:
            yield connection

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db_connection] = target_connection
    app.dependency_overrides[require_fixture_scope] = lambda: kept_scope

    async def check_mcp() -> None:
        async with session(kept_scope.group_key) as mcp:
            search = await mcp.call_tool("search_posts", {})
            assert str(removed_post) not in json.dumps(search.structured_content)
            deleted = await mcp.call_tool("get_post", {"post_id": str(removed_post)})
            assert deleted.is_error

    try:
        with TestClient(app) as client:
            cards = client.get("/v1/posts").json()["posts"]
            assert str(removed_post) not in json.dumps(cards)
            assert client.get(f"/v1/posts/{removed_post}").status_code == 404
            assert client.get(f"/v1/posts/{kept_post}/timeline").status_code == 200
            model_keys = {item["model_key"] for item in client.get("/v1/models").json()["models"]}
            assert "rtx-3070" not in model_keys
            trend = client.get("/v1/prices/trends", params={"model_key": "rtx-3070"}).json()
            assert trend["current"]["n"] == 0
        asyncio.run(check_mcp())
    finally:
        app.dependency_overrides.clear()


def test_create_snapshot_and_purge_share_the_media_advisory_lock(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root, _, backup_dir = backup_paths
    scope, _post_id, _evidence_id = seed_media_post(
        database_pair.source, media_root, "synthetic-o1a-lock"
    )
    with database_pair.source.connect() as connection:
        digest = connection.execute(select(media_assets.c.sha256)).scalar_one()

    copy_entered = Event()
    release_copy = Event()
    original_copy = backup_service._copy_asset_contents

    def pause_copy(*args: object) -> None:
        copy_entered.set()
        if not release_copy.wait(20):
            raise TimeoutError("backup media copy was not released")
        original_copy(*args)

    monkeypatch.setattr(backup_service, "_copy_asset_contents", pause_copy)
    with ThreadPoolExecutor(max_workers=2) as executor:
        backup_future = executor.submit(create_backup, database_pair.source)
        assert copy_entered.wait(20)
        revoke_grant(database_pair.source, scope.grant_id)
        purge_future = executor.submit(run_purge, database_pair.source, f"grant:{scope.grant_id}")
        deadline = time.monotonic() + 10
        waiting_for_asset_lock = False
        while time.monotonic() < deadline:
            with database_pair.source.connect() as connection:
                waiting_for_asset_lock = connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE locktype = 'advisory' AND granted = false)"
                    )
                ).scalar_one()
            if waiting_for_asset_lock:
                break
            time.sleep(0.05)
        try:
            assert waiting_for_asset_lock
            assert not purge_future.done()
        finally:
            release_copy.set()
        created = backup_future.result(timeout=30)
        purged = purge_future.result(timeout=30)

    assert created["state"] == "succeeded"
    assert purged["state"] == "succeeded"
    assert not media_path(media_root, digest, "image/webp").exists()
    archived_media = backup_dir / str(created["set_name"]) / "media" / digest[:2] / digest
    assert hashlib.sha256(archived_media.read_bytes()).hexdigest() == digest


def test_backup_snapshot_holds_media_copy_lock_against_committed_purge(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root, _, backup_dir = backup_paths
    scope, _post_id, _evidence_id = seed_media_post(
        database_pair.source, media_root, "synthetic-o1a-snapshot-purge-race"
    )
    with database_pair.source.connect() as connection:
        digest = connection.execute(select(media_assets.c.sha256)).scalar_one()

    revoke_grant(database_pair.source, scope.grant_id)
    snapshot_purge_ready = Event()
    resume_dump = Event()
    purge_futures = []
    state = {"purge_waiting_for_media_copy_lock": False}
    original_run_pg_dump = backup_service._run_pg_dump

    with ThreadPoolExecutor(max_workers=2) as executor:

        def purge_after_snapshot(database_url: URL, snapshot_id: str, destination: Path) -> int:
            purge_future = executor.submit(
                run_purge, database_pair.source, f"grant:{scope.grant_id}"
            )
            purge_futures.append(purge_future)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                with database_pair.source.connect() as connection:
                    media_row_exists = (
                        connection.execute(
                            select(media_assets.c.sha256).where(media_assets.c.sha256 == digest)
                        ).first()
                        is not None
                    )
                    purge_waiting = connection.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks "
                            "WHERE locktype = 'advisory' AND granted = false "
                            "AND database = (SELECT oid FROM pg_database "
                            "WHERE datname = current_database()))"
                        )
                    ).scalar_one()
                if not media_row_exists and (purge_waiting or purge_future.done()):
                    state["purge_waiting_for_media_copy_lock"] = bool(
                        purge_waiting and not purge_future.done()
                    )
                    snapshot_purge_ready.set()
                    if not resume_dump.wait(20):
                        raise TimeoutError("backup dump was not resumed after the purge")
                    return original_run_pg_dump(database_url, snapshot_id, destination)
                time.sleep(0.025)
            raise TimeoutError("purge did not commit or wait for the media-copy lock")

        monkeypatch.setattr(backup_service, "_run_pg_dump", purge_after_snapshot)
        backup_future = executor.submit(create_backup, database_pair.source)
        try:
            assert snapshot_purge_ready.wait(20)
        finally:
            resume_dump.set()
        assert len(purge_futures) == 1
        purge_future = purge_futures[0]
        created = backup_future.result(timeout=60)
        purged = purge_future.result(timeout=60)

    assert state["purge_waiting_for_media_copy_lock"] is True
    assert created["state"] == "succeeded"
    assert purged["state"] == "succeeded"
    assert purged["result_counts"]["files_deleted"] == 1
    assert purged["result_counts"]["pending_storage_keys"] == []
    assert not media_path(media_root, digest, "image/webp").exists()
    set_path = backup_dir / str(created["set_name"])
    manifest = json.loads((set_path / "manifest.json").read_text(encoding="utf-8"))
    assert digest in {item["sha256"] for item in manifest["media"]}
    archived_media = set_path / "media" / digest[:2] / digest
    assert hashlib.sha256(archived_media.read_bytes()).hexdigest() == digest


def test_backup_excludes_media_purged_before_backup_starts(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
) -> None:
    media_root, _, backup_dir = backup_paths
    scope, _post_id, _evidence_id = seed_media_post(
        database_pair.source, media_root, "synthetic-o1a-purge-before-backup"
    )
    with database_pair.source.connect() as connection:
        digest = connection.execute(select(media_assets.c.sha256)).scalar_one()

    revoke_grant(database_pair.source, scope.grant_id)
    purged = run_purge(database_pair.source, f"grant:{scope.grant_id}")
    assert purged["state"] == "succeeded"
    assert not media_path(media_root, digest, "image/webp").exists()

    created = create_backup(database_pair.source)
    set_path = backup_dir / str(created["set_name"])
    manifest = json.loads((set_path / "manifest.json").read_text(encoding="utf-8"))
    assert created["media_count"] == 0
    assert digest not in {item["sha256"] for item in manifest["media"]}


def test_restore_marks_backup_run_succeeded_at_manifest_time(
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
    tmp_path: Path,
) -> None:
    media_root, _, backup_dir = backup_paths
    seed_media_post(database_pair.source, media_root, "synthetic-o1a-restore-run-status")
    created = create_backup(database_pair.source)
    set_path = backup_dir / str(created["set_name"])
    manifest = json.loads((set_path / "manifest.json").read_text(encoding="utf-8"))
    expected_completed_at = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))

    restore_backup(
        database_pair.source,
        set_path=set_path,
        target_database_url=database_pair.target_url.set(password=None).render_as_string(
            hide_password=False
        ),
        target_media_root=tmp_path / "restore-run-status-media",
    )

    status = backup_status(database_pair.target)
    assert status["last_success"] is not None
    assert status["last_success"]["backup_run_id"] == created["backup_run_id"]
    assert status["last_success"]["completed_at"] == manifest["created_at"]
    with database_pair.target.connect() as connection:
        restored_run = connection.execute(
            select(backup_runs.c.state, backup_runs.c.completed_at).where(
                backup_runs.c.id == created["backup_run_id"]
            )
        ).one()
    assert restored_run.state == "succeeded"
    assert restored_run.completed_at == expected_completed_at
