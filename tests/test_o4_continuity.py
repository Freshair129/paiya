"""Process-level continuity checks using synthetic feed fixtures and PostgreSQL 17."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from test_a2_ingest import SAMPLE

from paiya.groups.service import add_group
from paiya.store.media import media_path
from paiya.store.tables import (
    capture_gaps,
    capture_runs,
    evidence,
    jobs,
    media_assets,
    metadata,
    observations,
    sale_events,
    status_facts,
)

ROOT = Path(__file__).resolve().parents[1]
WEBP = base64.b64decode("UklGRi4AAABXRUJQVlA4ICIAAABQAQCdASoCAAIAAkA4JQBOgCgAAP7tBywJK1ZulhhMGnAA")


@pytest.fixture(scope="module")
def o4_engine() -> Iterator[Engine]:
    raw_url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    if not raw_url or not raw_url.startswith("postgresql+psycopg://"):
        pytest.fail("PAIYA_TEST_DATABASE_URL must name a PostgreSQL 17 database")
    base_url = make_url(raw_url)
    if base_url.database != "paiya_test":
        pytest.fail("O4 tests require the isolated paiya_test database")
    admin = create_engine(base_url)
    schema = f"paiya_test_o4_{uuid4().hex}"
    with admin.begin() as connection:
        version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
        assert version // 10000 == 17
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url)
    previous_url = os.environ.get("PAIYA_DATABASE_URL")
    os.environ["PAIYA_DATABASE_URL"] = scoped_url.render_as_string(hide_password=False)
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        if previous_url is None:
            os.environ.pop("PAIYA_DATABASE_URL", None)
        else:
            os.environ["PAIYA_DATABASE_URL"] = previous_url
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def o4_db(o4_engine: Engine) -> Iterator[Engine]:
    table_names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
    with o4_engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE")
    yield o4_engine


def _next_slot() -> datetime:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    minute = (now.minute // 30) * 30
    return now.replace(minute=minute)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_feed(
    feed_root: Path,
    group_key: str,
    *,
    at: datetime,
    with_image: bool = True,
    image_data: bytes = WEBP,
) -> Path:
    scenario = feed_root / group_key
    envelopes = scenario / "envelopes"
    envelopes.mkdir(parents=True)
    envelope = json.loads(SAMPLE.read_text(encoding="utf-8"))
    envelope["run_id"] = str(uuid4())
    envelope["capture_id"] = str(uuid4())
    payload = envelope["payload"]
    payload["group_key"] = group_key
    payload["source_post_key"] = f"{group_key}-post"
    payload["observed_at"] = _iso(at)
    seller = payload.get("seller")
    if isinstance(seller, dict):
        seller["identity_scope"] = group_key
        seller["external_actor_key"] = f"{group_key}-seller"
    item = payload["items"][0]
    item["model"] = "RTX 3060"
    item["price"] = {
        "kind": "ask",
        "unit": "each",
        "amount": "5500",
        "currency": "THB",
    }
    payload["evidence"][0]["source_span"]["quote"] = "RTX 3060 5500 synthetic listing"
    if with_image:
        local_key = payload["evidence"][0]["local_key"]
        envelope["assets"] = {
            local_key: {
                "staging_key": "o4_image_webp",
                "sha256": hashlib.sha256(image_data).hexdigest(),
                "mime": "image/webp",
                "width": 2,
                "height": 2,
                "bytes": len(image_data),
            }
        }
        payload["evidence"][0]["media_state"] = "captured"
        asset_dir = scenario / "assets"
        asset_dir.mkdir()
        (asset_dir / "o4_image_webp").write_bytes(image_data)
    envelope_path = envelopes / "r1.json"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    document = {
        "feed_version": 1,
        "group_key": group_key,
        "runs": [
            {
                "label": "r1",
                "started_at": _iso(at),
                "session": "ok",
                "sort": "verified_latest",
                "access": "ok",
                "load_limit": None,
                "cards": [
                    {
                        "source_post_key": payload["source_post_key"],
                        "pinned": False,
                        "suggested": False,
                        "parse": "ok",
                        "envelope": "envelopes/r1.json",
                    }
                ],
            }
        ],
    }
    feed_path = scenario / "feed.json"
    feed_path.write_text(json.dumps(document), encoding="utf-8")
    return feed_path


def _base_env(pg_engine: Engine, tmp_path: Path, group_key: str) -> dict[str, str]:
    environment = os.environ.copy()
    for key in ("PAIYA_FAULT_POINT", "PAIYA_ALLOW_FAULT_INJECTION", "PAIYA_PURGE_LOG_PATH"):
        environment.pop(key, None)
    environment.update(
        {
            "PAIYA_DATABASE_URL": pg_engine.url.render_as_string(hide_password=False),
            "PAIYA_FIXTURE_FEED_DIR": str(tmp_path / "feeds"),
            "PAIYA_FIXTURE_MEDIA_ROOT": str(tmp_path / "media"),
            "PAIYA_READ_CONTEXT_GROUP_KEY": group_key,
            "UV_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", ""),
        }
    )
    Path(environment["PAIYA_FIXTURE_MEDIA_ROOT"]).mkdir(parents=True, exist_ok=True)
    Path(environment["PAIYA_FIXTURE_FEED_DIR"]).mkdir(parents=True, exist_ok=True)
    return environment


def _worker(
    environment: dict[str, str],
    now: datetime,
    *,
    fault: str | None = None,
    allow_fault: bool = True,
) -> subprocess.CompletedProcess[str]:
    child_env = environment.copy()
    child_env.pop("PAIYA_FAULT_POINT", None)
    child_env.pop("PAIYA_ALLOW_FAULT_INJECTION", None)
    if fault is not None:
        child_env["PAIYA_FAULT_POINT"] = fault
    if allow_fault and fault is not None:
        child_env["PAIYA_ALLOW_FAULT_INJECTION"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "paiya.cli",
            "worker",
            "run",
            "--once",
            "--now",
            _iso(now),
        ],
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _add_group(engine: Engine, group_key: str, now: datetime) -> UUID:
    with engine.connect() as connection:
        added = add_group(connection, now, group_key, 30)
    return UUID(added["id"])


def _counts(engine: Engine) -> dict[str, int]:
    tables = {
        "jobs": jobs,
        "capture_runs": capture_runs,
        "observations": observations,
        "status_facts": status_facts,
        "sale_events": sale_events,
        "evidence": evidence,
    }
    with engine.connect() as connection:
        return {
            name: int(connection.execute(select(func.count()).select_from(table)).scalar_one())
            for name, table in tables.items()
        }


def _orphan_files(engine: Engine, media_root: Path) -> list[Path]:
    with engine.connect() as connection:
        referenced = {
            media_path(media_root, row["storage_key"], row["mime"]).resolve()
            for row in connection.execute(select(media_assets.c.storage_key, media_assets.c.mime))
            .mappings()
            .all()
        }
    return [
        path
        for path in media_root.rglob("*")
        if path.is_file() and path.resolve() not in referenced
    ]


def test_worker_once_processes_one_synthetic_discovery(o4_db, tmp_path):
    pg_engine = o4_db
    now = _next_slot()
    group_key = "synthetic-o4-one-cycle"
    _add_group(pg_engine, group_key, now)
    _write_feed(tmp_path / "feeds", group_key, at=now)
    environment = _base_env(pg_engine, tmp_path, group_key)

    first = _worker(environment, now)
    assert first.returncode == 0, first.stderr
    first_result = json.loads(first.stdout)["result"]
    assert first_result["state"] == "succeeded"
    assert _counts(pg_engine)["capture_runs"] == 1
    assert _counts(pg_engine)["observations"] == 1

    second = _worker(environment, now)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["result"]["state"] == "idle"
    assert _counts(pg_engine)["capture_runs"] == 1


def test_fault_hook_gating_and_worker_refusal_cases(o4_db, tmp_path):
    pg_engine = o4_db
    code = "from paiya.worker.faults import maybe_fault; maybe_fault('after_media_write')"
    for allow, point, expected in (
        (None, None, 0),
        ("1", None, 0),
        (None, "after_media_write", 0),
        ("1", "before_job_complete", 0),
        ("1", "after_media_write", 86),
    ):
        environment = os.environ.copy()
        environment.pop("PAIYA_ALLOW_FAULT_INJECTION", None)
        environment.pop("PAIYA_FAULT_POINT", None)
        environment["PYTHONPATH"] = str(ROOT / "src")
        if allow is not None:
            environment["PAIYA_ALLOW_FAULT_INJECTION"] = allow
        if point is not None:
            environment["PAIYA_FAULT_POINT"] = point
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == expected

    group_key = "synthetic-o4-refusal"
    environment = _base_env(pg_engine, tmp_path, group_key)
    unsafe = environment.copy()
    unsafe["PAIYA_FAULT_POINT"] = "after_media_write"
    denied = _worker_with_environment(unsafe, _next_slot())
    assert denied.returncode == 2
    assert "fault_injection_not_allowed" in denied.stderr

    invalid = environment.copy()
    invalid["PAIYA_FAULT_POINT"] = "not-a-contract-point"
    invalid["PAIYA_ALLOW_FAULT_INJECTION"] = "1"
    denied = _worker_with_environment(invalid, _next_slot())
    assert denied.returncode == 2
    assert "fault_point_invalid" in denied.stderr


def _worker_with_environment(
    environment: dict[str, str], now: datetime
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "paiya.cli", "worker", "run", "--once", "--now", _iso(now)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_worker_startup_refuses_pending_purge_without_writes(o4_db, tmp_path):
    pg_engine = o4_db
    group_key = "synthetic-o4-purge-refusal"
    environment = _base_env(pg_engine, tmp_path, group_key)
    log_path = tmp_path / "purge.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "id": str(uuid4()),
                "scope_kind": "grant",
                "scope_key": str(uuid4()),
                "requested_at": _iso(_next_slot()),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    environment["PAIYA_PURGE_LOG_PATH"] = str(log_path)
    before = _counts(pg_engine)

    result = _worker_with_environment(environment, _next_slot())

    assert result.returncode == 2
    assert "purge_replay_required" in result.stderr
    assert "1 pending" in result.stderr
    assert _counts(pg_engine) == before


@pytest.mark.parametrize(
    "point", ["after_media_write", "after_ingest_commit", "before_job_complete"]
)
def test_crash_points_replay_without_duplicate_rows_or_events(o4_db, tmp_path, point):
    pg_engine = o4_db
    now = _next_slot()
    group_key = f"synthetic-o4-{point}"
    group_id = _add_group(pg_engine, group_key, now)
    _write_feed(tmp_path / "feeds", group_key, at=now, with_image=True)
    environment = _base_env(pg_engine, tmp_path, group_key)
    crash = _worker(environment, now, fault=point)
    assert crash.returncode == 86
    after_crash = _counts(pg_engine)
    media_root = Path(environment["PAIYA_FIXTURE_MEDIA_ROOT"])
    if point == "after_media_write":
        assert len(_orphan_files(pg_engine, media_root)) == 1
    else:
        assert len(_orphan_files(pg_engine, media_root)) == 0

    requeue = _worker(environment, now + timedelta(minutes=6))
    assert requeue.returncode == 0, requeue.stderr
    assert json.loads(requeue.stdout)["result"]["state"] == "idle"
    retry = _worker(environment, now + timedelta(minutes=7))
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout)["result"]["state"] == "succeeded"
    after_retry = _counts(pg_engine)
    assert after_retry["observations"] == 1
    assert after_retry["jobs"] == 1
    assert after_retry["capture_runs"] == 1
    assert after_retry["status_facts"] >= 1
    assert after_retry["sale_events"] >= 0
    assert len(_orphan_files(pg_engine, media_root)) == 0

    if point != "after_media_write":
        assert after_crash["observations"] == 1
        assert after_retry["observations"] == after_crash["observations"]
        assert after_retry["status_facts"] == after_crash["status_facts"]
        assert after_retry["sale_events"] == after_crash["sale_events"]
    with pg_engine.connect() as connection:
        row = (
            connection.execute(
                select(jobs.c.logical_key, jobs.c.state, capture_runs.c.id)
                .select_from(jobs.join(capture_runs, capture_runs.c.job_id == jobs.c.id))
                .where(jobs.c.group_id == group_id)
            )
            .mappings()
            .one()
        )
        assert row["state"] == "succeeded"
        status_keys = connection.execute(
            select(status_facts.c.observation_id, status_facts.c.item_id, status_facts.c.fact_key)
        ).all()
        event_keys = connection.execute(
            select(sale_events.c.item_id, sale_events.c.cause_key)
        ).all()
        assert len(status_keys) == len(set(status_keys))
        assert len(event_keys) == len(set(event_keys))


def test_sleep_records_missed_slots_and_health_reports_one_follow_up(o4_db, tmp_path):
    pg_engine = o4_db
    now = _next_slot()
    group_key = "synthetic-o4-sleep"
    group_id = _add_group(pg_engine, group_key, now)
    _write_feed(tmp_path / "feeds", group_key, at=now)
    environment = _base_env(pg_engine, tmp_path, group_key)

    first = _worker(environment, now)
    assert first.returncode == 0, first.stderr
    returned = _worker(environment, now + timedelta(hours=3))
    assert returned.returncode == 0, returned.stderr
    assert json.loads(returned.stdout)["result"]["state"] == "succeeded"

    with pg_engine.connect() as connection:
        gaps = (
            connection.execute(select(capture_gaps).where(capture_gaps.c.group_id == group_id))
            .mappings()
            .all()
        )
        assert len(gaps) == 1
        assert gaps[0]["reason"] == "missed_slots"
        assert gaps[0]["gap_from"] < now + timedelta(hours=3)
        assert gaps[0]["gap_to"] == now + timedelta(hours=3)

    report = subprocess.run(
        [sys.executable, "-m", "paiya.cli", "health"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert report.returncode == 0, report.stderr
    health = json.loads(report.stdout)
    group_health = next(row for row in health["coverage"] if row["group_id"] == str(group_id))
    assert group_health["capture_gap_count"] == 1
    assert group_health["latest_gap_7d"]["reason"] == "missed_slots"

    assert _counts(pg_engine)["capture_runs"] == 2


def _without_fixture_schemas(schemas: set[str]) -> set[str]:
    return {
        name
        for name in schemas
        if re.fullmatch(r"(paiya_test_(o4_)?|paiya_migration_)[0-9a-f]{32}", name) is None
    }


def test_drill_schema_snapshot_excludes_only_fixture_schemas():
    schemas = {
        "paiya_test_" + "a" * 32,
        "paiya_test_o4_" + "b" * 32,
        "paiya_migration_" + "c" * 32,
        "paiya_test_xyz",
        "paiya_o4_leak_x",
        "paiya_test_" + "d" * 31,
        "paiya_test_" + "A" * 32,
        "paiya_test_" + "e" * 32 + "_extra",
    }
    assert _without_fixture_schemas(schemas) == {
        "paiya_test_xyz",
        "paiya_o4_leak_x",
        "paiya_test_" + "d" * 31,
        "paiya_test_" + "A" * 32,
        "paiya_test_" + "e" * 32 + "_extra",
    }


def test_continuity_drill_end_to_end():
    raw_url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    assert raw_url is not None
    base_url = make_url(raw_url)
    assert base_url.database == "paiya_test"
    admin = create_engine(base_url)
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    with admin.connect() as connection:
        before = set(
            connection.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE 'paiya_o4_drill_%'")
            ).scalars()
        )
        schemas_before = set(
            connection.execute(
                text(
                    "SELECT nspname FROM pg_namespace "
                    "WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'"
                )
            ).scalars()
        )
    result = subprocess.run(
        [sys.executable, "scripts/continuity_drill.py"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    admin.dispose()
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert all(step["passed"] for step in report["steps"])

    cleanup = create_engine(base_url)
    try:
        with cleanup.connect() as connection:
            remaining = set(
                connection.execute(
                    text("SELECT datname FROM pg_database WHERE datname LIKE 'paiya_o4_drill_%'")
                ).scalars()
            )
            schemas = set(
                connection.execute(
                    text(
                        "SELECT nspname FROM pg_namespace "
                        "WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'"
                    )
                ).scalars()
            )
    finally:
        cleanup.dispose()
    assert remaining == before
    assert _without_fixture_schemas(schemas) == _without_fixture_schemas(schemas_before)


def test_drill_rejects_duplicate_sale_event_keys(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import continuity_drill as drill

    instance = object.__new__(drill.ContinuityDrill)
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    before = MagicMock()
    before.mappings.return_value.one.return_value = {
        "id": 1,
        "logical_key": "key",
        "state": "leased",
    }
    after = MagicMock()
    after.mappings.return_value.one.return_value = {
        "id": 1,
        "logical_key": "key",
        "state": "succeeded",
    }
    statuses = MagicMock()
    statuses.all.return_value = [(1, 1, "fact")]
    events = MagicMock()
    events.all.return_value = [(1, "event"), (1, "event")]
    connection.execute.side_effect = [before, after, statuses, events]
    instance._create_database = MagicMock(return_value=("db", None, engine))
    instance._step_paths = MagicMock(return_value=SimpleNamespace(feed=tmp_path, media=tmp_path))
    instance._env = MagicMock(return_value={})
    instance._group = MagicMock()
    instance._worker = MagicMock(side_effect=[None, {"state": "idle"}, {"state": "succeeded"}])
    monkeypatch.setattr(drill, "_write_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(drill, "_orphan_files", lambda *args: [])
    monkeypatch.setattr(drill, "_table_count", lambda *args: 1)

    with pytest.raises(drill.DrillFailure, match="replay_not_idempotent"):
        instance._run_crash_step("after_ingest_commit")


def test_drill_cleanup_failure_makes_run_fail(monkeypatch, capsys):
    from unittest.mock import MagicMock

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import continuity_drill as drill

    instance = object.__new__(drill.ContinuityDrill)
    instance.admin_engine = MagicMock()
    connection = instance.admin_engine.connect.return_value.__enter__.return_value
    connection.exec_driver_sql.return_value.scalar_one.return_value = 170000
    instance.steps = []
    monkeypatch.setattr(
        instance,
        "_step",
        lambda name, action: instance.steps.append({"name": name, "passed": True}),
    )
    monkeypatch.setattr(instance, "cleanup", lambda: {"passed": False})

    assert instance.run() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["cleanup"]["passed"] is False


def test_restore_drill_checks_mcp_for_both_scopes(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import continuity_drill as drill

    instance = object.__new__(drill.ContinuityDrill)
    url = make_url("postgresql+psycopg://localhost/paiya_test")
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.side_effect = [0, 1]
    instance._create_database = MagicMock(return_value=("db", url, engine))
    (tmp_path / "set").mkdir()
    paths = SimpleNamespace(root=tmp_path, backup=tmp_path, feed=tmp_path)
    instance._step_paths = MagicMock(return_value=paths)
    instance._env = MagicMock(side_effect=lambda url, paths, group_key: {"group": group_key})
    instance._group = MagicMock()
    instance._worker = MagicMock(return_value={"state": "succeeded"})
    instance._read_ids = MagicMock(
        side_effect=lambda engine, key: SimpleNamespace(
            group_key=key, grant_id=uuid4(), post_id=uuid4()
        )
    )

    def cli(environment, args, **kwargs):
        if args[:2] == ["backup", "create"]:
            return {"result": {"set_name": "set"}}
        if args[:2] == ["backup", "restore"]:
            return {"result": {"ready": True, "purge_replay": {"pending_count": 0}}}
        return {"state": "succeeded"}

    instance._cli = MagicMock(side_effect=cli)
    instance._start_serve = MagicMock(return_value=1234)
    instance._http_scope_checks = MagicMock()
    instance._stop_all_processes = MagicMock()
    instance._run_mcp = MagicMock()
    monkeypatch.setattr(drill, "_write_feed", lambda *args, **kwargs: None)

    instance._run_restore_step()

    assert instance._run_mcp.call_count == 2
    keys = [call.args[1].group_key for call in instance._run_mcp.call_args_list]
    assert keys[0].startswith("synthetic-o4-removed-")
    assert keys[1].startswith("synthetic-o4-kept-")
