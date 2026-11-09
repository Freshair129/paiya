"""Synthetic O1b backup retention acceptance tests on PostgreSQL 17."""

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import insert
from test_o1a_backup import DatabasePair

from paiya import cli
from paiya.backup import prune as prune_service
from paiya.backup.service import create_backup
from paiya.store.tables import backup_runs

pytest_plugins = ["test_o1a_backup"]


def _craft_backup_set(
    engine,
    backup_dir: Path,
    created_at: datetime,
    *,
    state: str = "succeeded",
) -> str:
    run_id = uuid4()
    set_name = f"{created_at.strftime('%Y%m%dT%H%M%SZ')}-{run_id}"
    set_path = backup_dir / set_name
    set_path.mkdir(parents=True)
    dump = f"synthetic dump for {run_id}".encode("ascii")
    (set_path / "db.dump").write_bytes(dump)
    manifest = {
        "format_version": 1,
        "backup_run_id": str(run_id),
        "created_at": created_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "alembic_revision": "synthetic",
        "app_version": "synthetic",
        "db": {"file": "db.dump", "sha256": hashlib.sha256(dump).hexdigest(), "bytes": len(dump)},
        "media": [],
        "settings": {},
        "same_device_allowed": True,
    }
    (set_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(
            insert(backup_runs).values(
                id=run_id,
                started_at=created_at,
                completed_at=None if state == "running" else created_at + timedelta(seconds=1),
                state=state,
                error_code="synthetic_failure" if state == "failed" else None,
                set_name=set_name,
            )
        )
    return set_name


def _run_prune(monkeypatch, capsys, engine, *arguments: str) -> tuple[int, dict[str, object], str]:
    monkeypatch.setattr(cli, "get_database_engine", lambda: engine)
    exit_code = cli.main(["backup", "prune", *arguments])
    output = capsys.readouterr().out
    return exit_code, json.loads(output), output


def test_backup_prune_defaults_to_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    pg_tool_commands: None,
) -> None:
    _media_root, _purge_dir, backup_dir = backup_paths
    older = create_backup(database_pair.source)
    newer = create_backup(database_pair.source)
    older_name = str(older["set_name"])
    newer_name = str(newer["set_name"])
    files_before = {path.relative_to(backup_dir) for path in backup_dir.rglob("*")}
    monkeypatch.setattr(cli, "get_database_engine", lambda: database_pair.source)

    exit_code = cli.main(["backup", "prune"])
    output = json.loads(capsys.readouterr().out)
    explicit_exit, explicit_output, _explicit_stdout = _run_prune(
        monkeypatch, capsys, database_pair.source, "--dry-run"
    )
    assert exit_code == 0
    assert explicit_exit == 0
    assert output["ok"] is True
    assert output["result"]["mode"] == "dry_run"
    assert [item["set_name"] for item in output["result"]["keep"]] == [newer_name]
    assert [item["set_name"] for item in output["result"]["delete"]] == [older_name]
    assert explicit_output["result"] == output["result"]
    assert (backup_dir / older_name).is_dir()
    assert (backup_dir / newer_name).is_dir()
    assert {path.relative_to(backup_dir) for path in backup_dir.rglob("*")} == files_before


def test_retention_uses_seven_days_and_four_iso_weeks_across_year_rollover(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
) -> None:
    _media_root, _purge_dir, backup_dir = backup_paths

    def at(year: int, month: int, day: int, hour: int = 12) -> datetime:
        return datetime(year, month, day, hour, tzinfo=UTC)

    sets: dict[str, datetime] = {}
    for created_at in (
        at(2025, 12, 21),
        at(2025, 12, 22),
        at(2025, 12, 28),
        at(2025, 12, 29),
        at(2026, 1, 4),
        at(2026, 1, 5),
        at(2026, 1, 11),
        *(at(2026, 1, day) for day in range(12, 19)),
        at(2026, 1, 15, 8),
    ):
        set_name = _craft_backup_set(database_pair.source, backup_dir, created_at)
        sets[set_name] = created_at

    exit_code, output, _stdout = _run_prune(monkeypatch, capsys, database_pair.source)

    result = output["result"]
    keep_names = {item["set_name"] for item in result["keep"]}
    delete_names = {item["set_name"] for item in result["delete"]}
    jan_15_sets = sorted(
        (
            name
            for name, created_at in sets.items()
            if created_at.date() == datetime(2026, 1, 15).date()
        ),
        key=lambda name: sets[name],
    )
    recent_days = {datetime(2026, 1, day).date() for day in range(12, 19)}
    expected_keep = {
        max(
            (name for name, created_at in sets.items() if created_at.date() == day),
            key=lambda name: sets[name],
        )
        for day in recent_days
    } | {
        name
        for name, created_at in sets.items()
        if created_at in {at(2025, 12, 28), at(2026, 1, 4), at(2026, 1, 11)}
    }
    expected_delete = set(sets) - expected_keep
    assert exit_code == 0
    assert keep_names == expected_keep
    assert delete_names == expected_delete
    assert jan_15_sets[0] in delete_names
    assert jan_15_sets[1] in keep_names
    assert any(
        item["set_name"] == jan_15_sets[1] and "daily_retention" in item["reason"]
        for item in result["keep"]
    )
    assert result["totals"]["keep_sets"] == 10
    assert result["totals"]["delete_sets"] == 5


def test_apply_deletes_exactly_the_dry_run_plan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
) -> None:
    _media_root, _purge_dir, backup_dir = backup_paths
    created_at = datetime(2026, 2, 2, 12, tzinfo=UTC)
    names = [
        _craft_backup_set(
            database_pair.source,
            backup_dir,
            created_at.replace(hour=hour),
        )
        for hour in (10, 11, 12)
    ]
    dry_exit, dry_output, _dry_stdout = _run_prune(monkeypatch, capsys, database_pair.source)
    planned = [item["set_name"] for item in dry_output["result"]["delete"]]

    apply_exit, apply_output, _apply_stdout = _run_prune(
        monkeypatch, capsys, database_pair.source, "--apply"
    )

    applied = apply_output["result"]
    assert dry_exit == 0
    assert apply_exit == 0
    assert applied["state"] == "applied"
    assert [item["set_name"] for item in applied["delete"]] == planned
    assert all(item["result"] == "deleted" for item in applied["delete"])
    assert set(planned) == set(names[:-1])
    assert all(not (backup_dir / name).exists() for name in planned)
    assert (backup_dir / names[-1]).is_dir()
    assert applied["totals"]["deleted_sets"] == len(planned)


def test_prune_refuses_a_backup_directory_that_fails_o1a_checks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
) -> None:
    media_root, _purge_dir, _backup_dir = backup_paths
    invalid_dir = media_root / "backup-sets"
    monkeypatch.setenv("PAIYA_BACKUP_DIR", str(invalid_dir))

    exit_code, output, _stdout = _run_prune(monkeypatch, capsys, database_pair.source)

    assert exit_code == 1
    assert output["ok"] is False
    assert output["error"]["code"] == "backup_destination_not_permitted"
    assert not invalid_dir.exists()


def test_dry_run_does_not_create_a_missing_backup_directory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    purge_root = tmp_path / "purge"
    purge_root.mkdir()
    missing_dir = tmp_path / "backup-sets-not-created"
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(purge_root / "purge.jsonl"))
    monkeypatch.setenv("PAIYA_BACKUP_DIR", str(missing_dir))
    monkeypatch.setenv("PAIYA_BACKUP_ALLOW_SAME_DEVICE", "1")

    exit_code, output, _stdout = _run_prune(monkeypatch, capsys, database_pair.source)

    assert exit_code == 0
    assert output["result"]["mode"] == "dry_run"
    assert output["result"]["keep"] == []
    assert output["result"]["delete"] == []
    assert not missing_dir.exists()


def test_apply_ignores_temp_unknown_symlink_and_outside_entries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    _media_root, _purge_dir, backup_dir = backup_paths
    created_at = datetime(2026, 3, 3, 12, tzinfo=UTC)
    delete_name = _craft_backup_set(database_pair.source, backup_dir, created_at.replace(hour=10))
    keep_name = _craft_backup_set(database_pair.source, backup_dir, created_at.replace(hour=12))
    invalid_name = _craft_backup_set(database_pair.source, backup_dir, created_at.replace(hour=9))
    (backup_dir / invalid_name / "manifest.json").write_text("{broken", encoding="utf-8")
    temporary = backup_dir / ".interrupted-create.tmp"
    temporary.mkdir()
    (temporary / "partial.bin").write_bytes(b"temporary")
    unknown = backup_dir / "owner-notes"
    unknown.mkdir()
    (unknown / "keep.txt").write_text("leave this entry alone", encoding="utf-8")
    outside_root = tmp_path / "outside-backups"
    outside_root.mkdir()
    internal_alias_name = _craft_backup_set(
        database_pair.source,
        backup_dir,
        datetime(2025, 1, 1, 12, tzinfo=UTC),
    )
    internal_target = backup_dir / "manually-renamed-owner-set"
    (backup_dir / internal_alias_name).rename(internal_target)
    internal_link = backup_dir / internal_alias_name
    internal_link.symlink_to(internal_target, target_is_directory=True)
    outside_name = _craft_backup_set(
        database_pair.source,
        outside_root,
        datetime(2025, 1, 1, 12, tzinfo=UTC),
    )
    outside_set = outside_root / outside_name
    outside_hashes = {
        path.relative_to(outside_set): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in outside_set.rglob("*")
        if path.is_file()
    }
    outside_link = backup_dir / outside_name
    outside_link.symlink_to(outside_set, target_is_directory=True)
    files_before = (temporary / "partial.bin").read_bytes()
    unknown_before = (unknown / "keep.txt").read_bytes()

    exit_code, output, stdout = _run_prune(monkeypatch, capsys, database_pair.source, "--apply")

    result = output["result"]
    output_names = {item["set_name"] for item in (*result["keep"], *result["delete"])}
    assert exit_code == 0
    assert [item["set_name"] for item in result["delete"]] == [delete_name]
    assert outside_name not in output_names
    assert internal_alias_name not in output_names
    assert invalid_name not in output_names
    assert (backup_dir / delete_name).exists() is False
    assert (backup_dir / keep_name).is_dir()
    assert (backup_dir / invalid_name / "manifest.json").read_text(encoding="utf-8") == "{broken"
    assert (temporary / "partial.bin").read_bytes() == files_before
    assert (unknown / "keep.txt").read_bytes() == unknown_before
    assert internal_link.is_symlink()
    assert (internal_target / "manifest.json").is_file()
    assert outside_link.is_symlink()
    assert {
        path.relative_to(outside_set): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in outside_set.rglob("*")
        if path.is_file()
    } == outside_hashes
    assert str(tmp_path).casefold() not in stdout.casefold()


def test_containment_check_excludes_a_link_that_resolves_outside_backup_dir(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    _media_root, _purge_dir, backup_dir = backup_paths
    outside_root = tmp_path / "outside-backups"
    outside_root.mkdir()
    outside_name = _craft_backup_set(
        database_pair.source,
        outside_root,
        datetime(2024, 1, 1, 12, tzinfo=UTC),
    )
    backup_dir.mkdir()
    outside_link = backup_dir / outside_name
    outside_link.symlink_to(outside_root / outside_name, target_is_directory=True)
    original_check = prune_service._is_link_or_reparse_point

    def emulate_unreported_junction(path: Path) -> bool:
        if path == outside_link:
            return False
        return original_check(path)

    monkeypatch.setattr(prune_service, "_is_link_or_reparse_point", emulate_unreported_junction)

    exit_code, output, _stdout = _run_prune(monkeypatch, capsys, database_pair.source)

    output_names = {
        item["set_name"] for item in (*output["result"]["keep"], *output["result"]["delete"])
    }
    assert exit_code == 0
    assert outside_name not in output_names
    assert outside_link.is_symlink()
    assert (outside_root / outside_name).is_dir()


def test_prune_keeps_latest_success_and_newest_set_outside_retention_windows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
) -> None:
    _media_root, _purge_dir, backup_dir = backup_paths
    latest_success = _craft_backup_set(
        database_pair.source,
        backup_dir,
        datetime(2024, 1, 1, 12, tzinfo=UTC),
    )
    recent_names = [
        _craft_backup_set(
            database_pair.source,
            backup_dir,
            datetime(2026, 1, day, 12, tzinfo=UTC),
            state="failed",
        )
        for day in (5, 12, 19, 26)
    ]
    recent_names.extend(
        _craft_backup_set(
            database_pair.source,
            backup_dir,
            datetime(2026, 2, day, 12, tzinfo=UTC),
            state="failed",
        )
        for day in range(2, 7)
    )
    newest_name = recent_names[-1]

    exit_code, output, _stdout = _run_prune(monkeypatch, capsys, database_pair.source)

    result = output["result"]
    keep_items = {item["set_name"]: item for item in result["keep"]}
    delete_names = {item["set_name"] for item in result["delete"]}
    assert exit_code == 0
    assert latest_success in keep_items
    assert "latest_successful_backup" in keep_items[latest_success]["reason"]
    assert newest_name in keep_items
    assert "newest_overall" in keep_items[newest_name]["reason"]
    assert recent_names[0] in delete_names


def test_prune_keeps_a_set_with_a_running_backup_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
) -> None:
    _media_root, _purge_dir, backup_dir = backup_paths
    running_name = _craft_backup_set(
        database_pair.source,
        backup_dir,
        datetime(2024, 1, 1, 12, tzinfo=UTC),
        state="running",
    )
    for day in (5, 12, 19, 26):
        _craft_backup_set(
            database_pair.source,
            backup_dir,
            datetime(2026, 1, day, 12, tzinfo=UTC),
            state="failed",
        )
    for day in range(2, 7):
        _craft_backup_set(
            database_pair.source,
            backup_dir,
            datetime(2026, 2, day, 12, tzinfo=UTC),
            state="failed",
        )

    exit_code, output, _stdout = _run_prune(monkeypatch, capsys, database_pair.source)

    result = output["result"]
    keep_items = {item["set_name"]: item for item in result["keep"]}
    assert exit_code == 0
    assert running_name in keep_items
    assert "running_backup" in keep_items[running_name]["reason"]
    assert (backup_dir / running_name).is_dir()


def test_partial_apply_failure_reports_failed_and_remaining_set_names(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_pair: DatabasePair,
    backup_paths: tuple[Path, Path, Path],
) -> None:
    _media_root, _purge_dir, backup_dir = backup_paths
    created_at = datetime(2026, 4, 2, 12, tzinfo=UTC)
    names = [
        _craft_backup_set(
            database_pair.source,
            backup_dir,
            created_at.replace(hour=hour),
        )
        for hour in (10, 11, 12)
    ]
    original_rmtree = shutil.rmtree

    def fail_first_delete(path: str | Path, *args: object, **kwargs: object) -> None:
        if Path(path).name == names[0]:
            raise PermissionError("synthetic delete failure")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(prune_service.shutil, "rmtree", fail_first_delete)

    exit_code, output, stdout = _run_prune(monkeypatch, capsys, database_pair.source, "--apply")

    result = output["result"]
    assert exit_code == 1
    assert output["ok"] is False
    assert output["error"]["code"] == "backup_prune_partial_failure"
    assert result["state"] == "partial_failure"
    assert result["remaining"] == [names[0]]
    assert result["failures"] == [{"set_name": names[0], "error_code": "delete_or_fsync_failed"}]
    assert {item["set_name"] for item in result["delete"]} == set(names[:-1])
    assert result["delete"][0]["result"] == "failed"
    assert result["delete"][1]["result"] == "deleted"
    assert (backup_dir / names[0]).is_dir()
    assert not (backup_dir / names[1]).exists()
    assert (backup_dir / names[2]).is_dir()
    assert str(backup_dir).casefold() not in stdout.casefold()
