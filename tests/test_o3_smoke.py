"""PostgreSQL 17 integration tests for the O3 release smoke check."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

from paiya.demo import seed_demo

ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_test.py"


@pytest.fixture
def smoke_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Engine, URL]]:
    raw_url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    if not raw_url or not raw_url.startswith("postgresql+psycopg://"):
        pytest.fail("PAIYA_TEST_DATABASE_URL must point to PostgreSQL 17")

    admin_url = make_url(raw_url)
    if admin_url.database != "paiya_test":
        pytest.fail("O3 smoke tests may create temporary databases only from paiya_test")

    admin = create_engine(admin_url)
    database_name = f"paiya_o3_smoke_{uuid4().hex}"
    database_url = admin_url.set(database=database_name)
    engine: Engine | None = None
    try:
        with admin.connect() as connection:
            version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
        assert version // 10000 == 17, "O3 smoke tests require PostgreSQL 17"
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

        engine = create_engine(database_url)
        monkeypatch.setenv("PAIYA_DATABASE_URL", database_url.render_as_string(hide_password=False))
        yield engine, database_url
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
        admin.dispose()


def _seed_database(
    database: tuple[Engine, URL],
    monkeypatch: pytest.MonkeyPatch,
    media_root: Path,
    revision: str = "head",
) -> None:
    engine, database_url = database
    media_root.mkdir()
    monkeypatch.setenv("PAIYA_DATABASE_URL", database_url.render_as_string(hide_password=False))
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-demo")
    monkeypatch.delenv("PAIYA_BACKUP_DIR", raising=False)
    monkeypatch.delenv("PAIYA_PURGE_LOG_PATH", raising=False)
    command.upgrade(Config(str(ROOT / "alembic.ini")), revision)
    seed_demo(engine)


def _run_smoke(
    media_root: Path, extra_env: dict[str, str] | None = None
) -> tuple[int, dict[str, object]]:
    environment = os.environ.copy()
    environment["PAIYA_FIXTURE_MEDIA_ROOT"] = str(media_root)
    environment["PAIYA_READ_CONTEXT_GROUP_KEY"] = "synthetic-demo"
    environment.pop("PAIYA_DEV_FIXTURE_MODE", None)
    environment.pop("PAIYA_BACKUP_DIR", None)
    environment.pop("PAIYA_PURGE_LOG_PATH", None)
    if extra_env:
        environment.update(extra_env)
    source_path = str(ROOT / "src")
    previous_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path if not previous_path else os.pathsep.join((source_path, previous_path))
    )
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            "smoke script did not emit a JSON report "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    assert isinstance(report, dict)
    return result.returncode, report


def _checks_by_name(report: dict[str, object]) -> dict[str, dict[str, object]]:
    checks = report.get("checks")
    assert isinstance(checks, list)
    named_checks: dict[str, dict[str, object]] = {}
    for check in checks:
        assert isinstance(check, dict)
        name = check.get("name")
        assert isinstance(name, str)
        named_checks[name] = check
    return named_checks


def test_smoke_passes_on_migrated_demo_seeded_database(
    smoke_database: tuple[Engine, URL],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    _seed_database(smoke_database, monkeypatch, media_root)

    exit_code, report = _run_smoke(media_root)

    assert exit_code == 0
    assert report["ok"] is True
    assert all(check["ok"] for check in _checks_by_name(report).values())


def test_smoke_fails_when_database_is_one_migration_behind(
    smoke_database: tuple[Engine, URL],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    _seed_database(smoke_database, monkeypatch, media_root, "0009_purge_jobs")

    exit_code, report = _run_smoke(media_root)

    assert exit_code != 0
    assert report["ok"] is False
    assert _checks_by_name(report)["alembic_head"]["ok"] is False


def test_smoke_fails_when_purge_log_has_pending_commands(
    smoke_database: tuple[Engine, URL],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    _seed_database(smoke_database, monkeypatch, media_root)
    purge_id = "8e502b7a-0586-4cef-8430-ec17e8219f26"
    scope_key = "a6072a4a-6848-4760-b208-dcd4450602bc"
    purge_log = tmp_path / "purge.jsonl"
    purge_log.write_text(
        json.dumps(
            {
                "id": purge_id,
                "scope_kind": "grant",
                "scope_key": scope_key,
                "requested_at": "2026-11-09T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code, report = _run_smoke(
        media_root,
        {"PAIYA_PURGE_LOG_PATH": str(purge_log)},
    )

    assert exit_code != 0
    assert report["ok"] is False
    assert _checks_by_name(report)["purge_log"]["code"] == "purge_replay_required"
    assert scope_key not in json.dumps(report)
