"""Run the local O4 continuity drill against disposable PostgreSQL 17 databases."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import UUID, uuid4

from alembic import command as alembic_command
from alembic.config import Config
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paiya.store.media import media_path  # noqa: E402
from paiya.store.tables import (  # noqa: E402
    capture_gaps,
    capture_runs,
    evidence,
    groups,
    jobs,
    listing_items,
    media_assets,
    observations,
    posts,
    sale_events,
    source_grants,
    status_facts,
)

_WEBP = base64.b64decode("UklGRi4AAABXRUJQVlA4ICIAAABQAQCdASoCAAIAAkA4JQBOgCgAAP7tBywJK1ZulhhMGnAA")
_HELP = """O4 local-only continuity exercise. It creates uniquely named databases from
PAIYA_TEST_DATABASE_URL (which must name paiya_test on PostgreSQL 17), runs
fixture commands in separate processes, and drops only those databases afterward.

The backup step uses PAIYA_PG_DUMP and PAIYA_PG_RESTORE command strings. If they
are unset and the test URL uses port 55432, the drill creates temporary wrappers
that invoke pg_dump/pg_restore through `docker exec -i -e PGPASSWORD paiya-pg17`
and translate the host-mapped port to the container's 5432. This matches the
local wrapper used by tests/test_o1a_backup.py. Set both variables explicitly
when using another local PostgreSQL 17 tool location. No packages are installed
and no external network is used. The serve check binds an OS-selected loopback
port above 8000; it never uses port 8000.
"""


class DrillFailure(RuntimeError):
    """A failed contract step with a safe report code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StepPaths:
    root: Path
    feed: Path
    media: Path
    backup: Path
    purge_log: Path


@dataclass(frozen=True)
class ReadIds:
    group_key: str
    group_id: UUID
    grant_id: UUID
    post_id: UUID
    seller_id: UUID
    evidence_id: UUID
    model_key: str
    variant: str | None
    vram_gb: int | None
    condition: str | None


def _trend_arguments(ids: ReadIds) -> dict[str, object]:
    arguments: dict[str, object] = {"model_key": ids.model_key}
    if ids.variant is not None:
        arguments["variant"] = ids.variant
    if ids.vram_gb is not None:
        arguments["vram_gb"] = ids.vram_gb
    if ids.condition is not None:
        arguments["condition"] = ids.condition
    return arguments


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _next_slot() -> datetime:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    minute = (now.minute // 30) * 30
    return now.replace(minute=minute) + timedelta(minutes=30)


def _write_feed(
    feed_root: Path,
    group_key: str,
    at: datetime,
    *,
    image_data: bytes = _WEBP,
) -> None:
    scenario = feed_root / group_key
    envelopes = scenario / "envelopes"
    assets_dir = scenario / "assets"
    envelopes.mkdir(parents=True)
    assets_dir.mkdir()
    envelope_path = ROOT / "fixtures/scenarios/S01-reserve-reopen/rounds/01-open.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    payload = envelope["payload"]
    envelope["run_id"] = str(uuid4())
    envelope["capture_id"] = str(uuid4())
    payload["group_key"] = group_key
    payload["source_post_key"] = f"{group_key}-post"
    payload["observed_at"] = _iso(at)
    seller_value = payload.get("seller")
    if isinstance(seller_value, dict):
        seller_value["identity_scope"] = group_key
        seller_value["external_actor_key"] = f"{group_key}-seller"
    item = payload["items"][0]
    item["model"] = "RTX 3060"
    item["price"] = {
        "kind": "ask",
        "unit": "each",
        "amount": "5500",
        "currency": "THB",
    }
    payload["evidence"][0]["source_span"]["quote"] = "RTX 3060 5500 synthetic listing"
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
    (assets_dir / "o4_image_webp").write_bytes(image_data)
    (envelopes / "r1.json").write_text(json.dumps(envelope), encoding="utf-8")
    feed = {
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
    (scenario / "feed.json").write_text(json.dumps(feed), encoding="utf-8")


def _table_count(engine: Engine, table: Any) -> int:
    with engine.connect() as connection:
        return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


def _orphan_files(engine: Engine, root: Path) -> list[Path]:
    with engine.connect() as connection:
        references = {
            media_path(root, row["storage_key"], row["mime"]).resolve()
            for row in connection.execute(
                select(media_assets.c.storage_key, media_assets.c.mime)
            ).mappings()
        }
    return [path for path in root.rglob("*") if path.is_file() and path.resolve() not in references]


class ContinuityDrill:
    def __init__(self, root: Path, test_url: URL) -> None:
        if test_url.database != "paiya_test":
            raise DrillFailure("test_database_must_be_paiya_test")
        self.root = root
        self.test_url = test_url
        self.admin_engine = create_engine(test_url.set(query={}))
        self.engines: list[Engine] = [self.admin_engine]
        self.database_names: list[str] = []
        self.processes: list[subprocess.Popen[bytes]] = []
        self.process_logs: dict[int, Any] = {}
        self.steps: list[dict[str, object]] = []
        self.base_environment = os.environ.copy()
        for name in ("PAIYA_FAULT_POINT", "PAIYA_ALLOW_FAULT_INJECTION"):
            self.base_environment.pop(name, None)
        self.base_environment["UV_OFFLINE"] = "1"
        self.base_environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.base_environment["PYTHONPATH"] = (
            str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
        )
        if test_url.password is not None:
            self.base_environment["PGPASSWORD"] = test_url.password
        self._set_pg_tool_commands()

    def _set_pg_tool_commands(self) -> None:
        if self.base_environment.get("PAIYA_PG_DUMP") and self.base_environment.get(
            "PAIYA_PG_RESTORE"
        ):
            return
        if self.test_url.port != 55432:
            return
        proxy = self.root / "pg_tool_proxy.py"
        proxy.write_text(
            "import subprocess\n"
            "import sys\n"
            "from sqlalchemy.engine import make_url\n"
            "tool, *args = sys.argv[1:]\n"
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
        self.base_environment["PAIYA_PG_DUMP"] = shlex.join([sys.executable, str(proxy), "pg_dump"])
        self.base_environment["PAIYA_PG_RESTORE"] = shlex.join(
            [sys.executable, str(proxy), "pg_restore"]
        )

    def _step_paths(self, name: str) -> StepPaths:
        step_root = self.root / name
        backup_root = self.root / f"{name}-backups"
        step_root.mkdir()
        backup_root.mkdir()
        paths = StepPaths(
            root=step_root,
            feed=step_root / "feeds",
            media=step_root / "media",
            backup=backup_root,
            purge_log=step_root / "purge.jsonl",
        )
        paths.feed.mkdir()
        paths.media.mkdir()
        paths.purge_log.write_text("", encoding="utf-8")
        return paths

    def _create_database(self, tag: str, *, migrate: bool = True) -> tuple[str, URL, Engine]:
        name = f"paiya_o4_drill_{uuid4().hex[:10]}_{tag}"
        if len(name) > 63:
            raise DrillFailure("database_name_invalid")
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
        self.database_names.append(name)
        database_url = self.test_url.set(database=name)
        engine = create_engine(database_url)
        self.engines.append(engine)
        with engine.begin() as connection:
            version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
        if version // 10000 != 17:
            raise DrillFailure("postgresql_17_required")
        if migrate:
            previous = os.environ.get("PAIYA_DATABASE_URL")
            os.environ["PAIYA_DATABASE_URL"] = database_url.render_as_string(hide_password=False)
            try:
                alembic_command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
            finally:
                if previous is None:
                    os.environ.pop("PAIYA_DATABASE_URL", None)
                else:
                    os.environ["PAIYA_DATABASE_URL"] = previous
        return name, database_url, engine

    def _env(self, database_url: URL, paths: StepPaths, group_key: str) -> dict[str, str]:
        environment = self.base_environment.copy()
        environment.update(
            {
                "PAIYA_DATABASE_URL": database_url.render_as_string(hide_password=False),
                "PAIYA_FIXTURE_FEED_DIR": str(paths.feed),
                "PAIYA_FIXTURE_MEDIA_ROOT": str(paths.media),
                "PAIYA_READ_CONTEXT_GROUP_KEY": group_key,
                "PAIYA_PURGE_LOG_PATH": str(paths.purge_log),
                "PAIYA_BACKUP_DIR": str(paths.backup),
                "PAIYA_BACKUP_ALLOW_SAME_DEVICE": "1",
                "PAIYA_TREND_MIN_OFFERS": "1",
                "PAIYA_TREND_MIN_SELLERS": "1",
            }
        )
        return environment

    def _cli(
        self,
        environment: dict[str, str],
        arguments: list[str],
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        completed = subprocess.run(
            [sys.executable, "-m", "paiya.cli", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            error_code = "unknown"
            try:
                output = json.loads(completed.stdout)
                candidate = output.get("error", {}).get("code")
                if isinstance(candidate, str):
                    error_code = candidate
            except (json.JSONDecodeError, AttributeError):
                first_line = completed.stderr.splitlines()[0] if completed.stderr else ""
                candidate = first_line.split(":", 1)[0]
                if candidate and candidate.replace("_", "").isalnum():
                    error_code = candidate
            raise DrillFailure(f"cli_subprocess_failed_{error_code}")
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise DrillFailure("cli_output_invalid") from error
        if not isinstance(result, dict):
            raise DrillFailure("cli_output_invalid")
        return result

    def _worker(
        self,
        environment: dict[str, str],
        now: datetime,
        *,
        fault: str | None = None,
        expected_exit: int = 0,
    ) -> dict[str, Any] | None:
        child_env = environment.copy()
        child_env.pop("PAIYA_FAULT_POINT", None)
        child_env.pop("PAIYA_ALLOW_FAULT_INJECTION", None)
        if fault is not None:
            child_env["PAIYA_FAULT_POINT"] = fault
            child_env["PAIYA_ALLOW_FAULT_INJECTION"] = "1"
        completed = subprocess.run(
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
        if completed.returncode != expected_exit:
            error_code = "unknown"
            try:
                output = json.loads(completed.stdout)
                candidate = output.get("error", {}).get("code")
                if isinstance(candidate, str):
                    error_code = candidate
            except (json.JSONDecodeError, AttributeError):
                first_line = completed.stderr.splitlines()[0] if completed.stderr else ""
                candidate = first_line.split(":", 1)[0]
                if candidate and candidate.replace("_", "").isalnum():
                    error_code = candidate
            raise DrillFailure(f"worker_unexpected_exit_{error_code}")
        if expected_exit == 86:
            return None
        try:
            payload = json.loads(completed.stdout)
            result = payload["result"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise DrillFailure("worker_output_invalid") from error
        if not isinstance(result, dict):
            raise DrillFailure("worker_output_invalid")
        return cast(dict[str, Any], result)

    def _group(self, environment: dict[str, str], group_key: str) -> UUID:
        result = self._cli(environment, ["groups", "add", group_key, "--poll-minutes", "30"])
        try:
            return UUID(cast(str, result["result"]["id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise DrillFailure("group_seed_failed") from error

    def _run_crash_step(self, point: str) -> dict[str, object]:
        _, database_url, engine = self._create_database(f"crash_{point}")
        paths = self._step_paths(f"crash_{point}")
        now = _next_slot()
        group_key = f"synthetic-o4-{point}"
        environment = self._env(database_url, paths, group_key)
        self._group(environment, group_key)
        _write_feed(paths.feed, group_key, now)
        self._worker(environment, now, fault=point, expected_exit=86)

        with engine.connect() as connection:
            job_before = (
                connection.execute(
                    select(jobs.c.id, jobs.c.logical_key, jobs.c.state).where(
                        jobs.c.kind == "discover"
                    )
                )
                .mappings()
                .one()
            )
        orphans_after_crash = _orphan_files(engine, paths.media)
        if point == "after_media_write" and len(orphans_after_crash) != 1:
            raise DrillFailure("media_orphan_not_reported")
        if point != "after_media_write" and orphans_after_crash:
            raise DrillFailure("unexpected_media_orphan")

        # The first cycle after lease expiry applies the configured one-minute
        # retry delay; the next once-cycle claims the same job and capture run.
        requeue = self._worker(environment, now + timedelta(minutes=6))
        if requeue is None or requeue["state"] != "idle":
            raise DrillFailure("lease_requeue_failed")
        recovered = self._worker(environment, now + timedelta(minutes=7))
        if recovered is None or recovered["state"] != "succeeded":
            raise DrillFailure("crash_recovery_failed")
        with engine.connect() as connection:
            job_after = (
                connection.execute(
                    select(jobs.c.id, jobs.c.logical_key, jobs.c.state).where(
                        jobs.c.kind == "discover"
                    )
                )
                .mappings()
                .one()
            )
            status_keys = connection.execute(
                select(
                    status_facts.c.observation_id,
                    status_facts.c.item_id,
                    status_facts.c.fact_key,
                )
            ).all()
            event_keys = connection.execute(
                select(sale_events.c.item_id, sale_events.c.cause_key)
            ).all()
        counts = {
            "jobs": _table_count(engine, jobs),
            "capture_runs": _table_count(engine, capture_runs),
            "observations": _table_count(engine, observations),
            "status_facts": len(status_keys),
            "sale_events": len(event_keys),
        }
        if (
            counts["jobs"] != 1
            or counts["capture_runs"] != 1
            or counts["observations"] != 1
            or job_before["id"] != job_after["id"]
            or job_before["logical_key"] != job_after["logical_key"]
            or job_after["state"] != "succeeded"
            or len(status_keys) != len(set(status_keys))
            or len(event_keys) != len(set(event_keys))
        ):
            raise DrillFailure("replay_not_idempotent")
        remaining_orphans = _orphan_files(engine, paths.media)
        if remaining_orphans:
            raise DrillFailure("media_orphan_remains")
        if orphans_after_crash and not orphans_after_crash[0].is_file():
            raise DrillFailure("orphan_was_removed")
        return {
            "fault_point": point,
            "same_job_key": True,
            "row_counts": counts,
            "orphan_files_reported_after_crash": len(orphans_after_crash),
            "orphan_files_deleted_by_drill": 0,
            "recovery_cycles_after_expiry": 2,
        }

    def _run_sleep_step(self) -> dict[str, object]:
        _, database_url, engine = self._create_database("sleep")
        paths = self._step_paths("sleep")
        now = _next_slot()
        group_key = "synthetic-o4-sleep"
        environment = self._env(database_url, paths, group_key)
        group_id = self._group(environment, group_key)
        _write_feed(paths.feed, group_key, now)
        first = self._worker(environment, now)
        second = self._worker(environment, now + timedelta(hours=3))
        if first is None or first["state"] != "succeeded":
            raise DrillFailure("initial_sleep_cycle_failed")
        if second is None or second["state"] != "succeeded":
            raise DrillFailure("sleep_recovery_cycle_failed")
        with engine.connect() as connection:
            gaps = (
                connection.execute(select(capture_gaps).where(capture_gaps.c.group_id == group_id))
                .mappings()
                .all()
            )
        if len(gaps) != 1 or gaps[0]["reason"] != "missed_slots":
            raise DrillFailure("missed_slots_gap_missing")

        port = self._start_serve(environment, paths.root / "sleep-serve.log")
        health = self._http_json(port, "/v1/admin/health")
        group_health = next(
            (row for row in health["coverage"] if row["group_id"] == str(group_id)),
            None,
        )
        if (
            group_health is None
            or group_health["capture_gap_count"] != 1
            or group_health["latest_gap_7d"]["reason"] != "missed_slots"
        ):
            raise DrillFailure("health_does_not_report_gap")
        self._stop_all_processes()

        if _table_count(engine, capture_runs) != 2:
            raise DrillFailure("sleep_cycle_count_invalid")
        return {
            "gap_reason": "missed_slots",
            "gap_from": _iso(cast(datetime, gaps[0]["gap_from"])),
            "gap_to": _iso(cast(datetime, gaps[0]["gap_to"])),
            "follow_up_cycles": 1,
            "health_reports_gap": True,
            "serve_port_above_8000": True,
        }

    def _read_ids(self, engine: Engine, group_key: str) -> ReadIds:
        with engine.connect() as connection:
            group_id = connection.execute(
                select(groups.c.id).where(groups.c.group_key == group_key)
            ).scalar_one_or_none()
            if group_id is None:
                raise DrillFailure("restore_group_missing")
            grant_id = connection.execute(
                select(source_grants.c.id).where(source_grants.c.group_id == group_id)
            ).scalar_one_or_none()
            post_id = connection.execute(
                select(posts.c.id).where(posts.c.group_id == group_id)
            ).scalar_one_or_none()
            if grant_id is None or post_id is None:
                raise DrillFailure("restore_fixture_missing")
            seller_id = connection.execute(
                select(posts.c.seller_id).where(posts.c.id == post_id)
            ).scalar_one_or_none()
            evidence_id = connection.execute(
                select(evidence.c.id).where(evidence.c.post_id == post_id).limit(1)
            ).scalar_one_or_none()
            item = (
                connection.execute(
                    select(
                        listing_items.c.model_key,
                        listing_items.c.variant,
                        listing_items.c.vram_gb,
                        listing_items.c.condition,
                    )
                    .where(
                        listing_items.c.post_id == post_id, listing_items.c.model_key.is_not(None)
                    )
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if seller_id is None or evidence_id is None or item is None:
            raise DrillFailure("restore_read_fixture_incomplete")
        return ReadIds(
            group_key=group_key,
            group_id=cast(UUID, group_id),
            grant_id=cast(UUID, grant_id),
            post_id=cast(UUID, post_id),
            seller_id=cast(UUID, seller_id),
            evidence_id=cast(UUID, evidence_id),
            model_key=cast(str, item["model_key"]),
            variant=cast(str | None, item["variant"]),
            vram_gb=cast(int | None, item["vram_gb"]),
            condition=cast(str | None, item["condition"]),
        )

    def _free_port(self) -> int:
        for _ in range(10):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
                candidate.bind(("127.0.0.1", 0))
                port = int(candidate.getsockname()[1])
            if port > 8000:
                return port
        raise DrillFailure("loopback_port_unavailable")

    def _start_serve(self, environment: dict[str, str], log_path: Path) -> int:
        port = self._free_port()
        child_env = environment.copy()
        child_env["PAIYA_PORT"] = str(port)
        log = log_path.open("wb")
        process = subprocess.Popen(
            [sys.executable, "-m", "paiya.serve"],
            cwd=ROOT,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        self.processes.append(process)
        self.process_logs[process.pid] = log
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise DrillFailure("serve_exited_before_ready")
            try:
                with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                    if response.status == 200:
                        return port
            except (OSError, URLError):
                time.sleep(0.1)
        raise DrillFailure("serve_start_timeout")

    def _stop_all_processes(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log = self.process_logs.pop(process.pid, None)
            if log is not None:
                log.close()
        self.processes.clear()

    def _http(self, port: int, path: str) -> tuple[int, bytes]:
        try:
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=4) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()
        except (OSError, URLError) as error:
            raise DrillFailure("local_api_unavailable") from error

    def _http_json(self, port: int, path: str) -> dict[str, Any]:
        status, body = self._http(port, path)
        if status != 200:
            raise DrillFailure("local_api_request_failed")
        try:
            value = json.loads(body)
        except json.JSONDecodeError as error:
            raise DrillFailure("local_api_json_invalid") from error
        if not isinstance(value, dict):
            raise DrillFailure("local_api_json_invalid")
        return cast(dict[str, Any], value)

    def _http_scope_checks(self, port: int, ids: ReadIds, *, readable: bool) -> None:
        posts_status, posts_body = self._http(port, "/v1/posts")
        post_status, post_body = self._http(port, f"/v1/posts/{ids.post_id}")
        seller_status, seller_body = self._http(port, f"/v1/sellers/{ids.seller_id}")
        evidence_status, evidence_body = self._http(port, f"/v1/evidence/{ids.evidence_id}")
        media_status, media_body = self._http(port, f"/v1/evidence/{ids.evidence_id}/media")
        model_status, model_body = self._http(port, "/v1/models")
        trend_query = urlencode(_trend_arguments(ids))
        trend_status, trend_body = self._http(port, f"/v1/prices/trends?{trend_query}")
        if readable:
            if posts_status != 200 or str(ids.post_id) not in posts_body.decode("utf-8"):
                raise DrillFailure("permitted_api_search_failed")
            if any(
                status != 200
                for status in (
                    post_status,
                    seller_status,
                    evidence_status,
                    media_status,
                    model_status,
                    trend_status,
                )
            ):
                raise DrillFailure("permitted_api_read_failed")
            trend = json.loads(trend_body)
            current = trend.get("current") if isinstance(trend, dict) else None
            if not isinstance(current, dict) or not isinstance(current.get("n"), int):
                raise DrillFailure("permitted_api_trend_invalid")
            if current["n"] < 1:
                raise DrillFailure("permitted_api_trend_missing")
            if ids.model_key not in model_body.decode("utf-8"):
                raise DrillFailure("permitted_api_model_missing")
            return

        if any(
            status >= 500 for status in (posts_status, post_status, seller_status, evidence_status)
        ):
            raise DrillFailure("revoked_api_unavailable")
        if str(ids.post_id) in posts_body.decode("utf-8") + post_body.decode("utf-8"):
            raise DrillFailure("revoked_post_readable_over_api")
        if str(ids.seller_id) in seller_body.decode("utf-8"):
            raise DrillFailure("revoked_seller_readable_over_api")
        if str(ids.evidence_id) in evidence_body.decode("utf-8"):
            raise DrillFailure("revoked_evidence_readable_over_api")
        if media_status not in (403, 404):
            raise DrillFailure("revoked_media_not_hidden")
        if trend_status == 200 and json.loads(trend_body)["current"]["n"] != 0:
            raise DrillFailure("revoked_trend_readable_over_api")
        if model_status == 200 and ids.model_key in model_body.decode("utf-8"):
            raise DrillFailure("revoked_model_readable_over_api")

    async def _mcp_scope_checks(self, environment: dict[str, str], ids: ReadIds) -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "paiya.mcp"],
            env=environment,
        )
        async with asyncio.timeout(25):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    names = {tool.name for tool in tools.tools}
                    if names != {
                        "search_posts",
                        "get_post",
                        "get_seller",
                        "get_evidence",
                        "get_price_trend",
                    }:
                        raise DrillFailure("mcp_tool_set_invalid")
                    requests = (
                        ("search_posts", {}),
                        ("get_post", {"post_id": str(ids.post_id)}),
                        ("get_seller", {"seller_id": str(ids.seller_id)}),
                        ("get_evidence", {"evidence_id": str(ids.evidence_id)}),
                        ("get_price_trend", _trend_arguments(ids)),
                    )
                    results = []
                    for name, arguments in requests:
                        results.append(await client.call_tool(name, arguments))
        if ids.group_key.startswith("synthetic-o4-removed-"):
            for (name, _), result in zip(requests, results, strict=True):
                content = json.dumps(result.structured_content, ensure_ascii=False)
                if result.is_error:
                    continue
                if name in {"get_post", "get_seller", "get_evidence"}:
                    raise DrillFailure("revoked_mcp_item_readable")
                if str(ids.post_id) in content or str(ids.seller_id) in content:
                    raise DrillFailure("revoked_mcp_data_readable")
                if name == "get_price_trend" and result.structured_content["current"]["n"] != 0:
                    raise DrillFailure("revoked_mcp_trend_readable")
            return
        for (name, _), result in zip(requests, results, strict=True):
            if result.is_error:
                raise DrillFailure("permitted_mcp_read_failed")
            content = json.dumps(result.structured_content, ensure_ascii=False)
            if name == "search_posts" and str(ids.post_id) not in content:
                raise DrillFailure("permitted_mcp_search_missing")
            if name == "get_post" and str(ids.post_id) not in content:
                raise DrillFailure("permitted_mcp_post_missing")
            if name == "get_seller" and str(ids.seller_id) not in content:
                raise DrillFailure("permitted_mcp_seller_missing")
            if name == "get_evidence" and str(ids.evidence_id) not in content:
                raise DrillFailure("permitted_mcp_evidence_missing")
            if name == "get_price_trend":
                trend = result.structured_content
                current = trend.get("current") if isinstance(trend, dict) else None
                if not isinstance(current, dict) or not isinstance(current.get("n"), int):
                    raise DrillFailure("permitted_mcp_trend_invalid")
                if current["n"] < 1:
                    raise DrillFailure("permitted_mcp_trend_missing")

    def _run_mcp(self, environment: dict[str, str], ids: ReadIds) -> None:
        asyncio.run(self._mcp_scope_checks(environment, ids))

    def _run_restore_step(self) -> dict[str, object]:
        _, source_url, source_engine = self._create_database("restore_source")
        _, target_url, target_engine = self._create_database("restore_target", migrate=False)
        paths = self._step_paths("restore")
        feed_at = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(days=1)
        kept_key = f"synthetic-o4-kept-{uuid4().hex[:6]}"
        removed_key = f"synthetic-o4-removed-{uuid4().hex[:6]}"
        kept_env = self._env(source_url, paths, kept_key)
        removed_env = self._env(source_url, paths, removed_key)
        self._group(kept_env, kept_key)
        self._group(removed_env, removed_key)
        now = datetime.now(UTC) + timedelta(seconds=1)
        _write_feed(paths.feed, kept_key, feed_at)
        _write_feed(paths.feed, removed_key, feed_at, image_data=_WEBP + b"synthetic-o4-removed")
        for _ in range(2):
            result = self._worker(kept_env, now)
            if result is None or result["state"] not in {"succeeded", "idle"}:
                raise DrillFailure("restore_fixture_worker_failed")
        kept = self._read_ids(source_engine, kept_key)
        removed = self._read_ids(source_engine, removed_key)

        backup = self._cli(kept_env, ["backup", "create"])
        try:
            set_path = paths.backup / cast(str, backup["result"]["set_name"])
        except (KeyError, TypeError) as error:
            raise DrillFailure("backup_set_missing") from error
        if not set_path.is_dir():
            raise DrillFailure("backup_set_missing")
        revoked = self._cli(removed_env, ["grant", "revoke", str(removed.grant_id)])
        if not revoked:
            raise DrillFailure("grant_revoke_failed")
        purged = self._cli(
            removed_env,
            ["purge", "run", "--scope", f"grant:{removed.grant_id}"],
        )
        if purged.get("state") != "succeeded":
            raise DrillFailure("grant_purge_failed")

        target_env = self._env(
            source_url,
            paths,
            kept_key,
        )
        restored = self._cli(
            target_env,
            [
                "backup",
                "restore",
                "--set",
                str(set_path),
                "--target-database-url",
                target_url.set(password=None).render_as_string(hide_password=False),
                "--target-media-root",
                str(paths.root / "restored-media"),
            ],
            timeout=60,
        )
        restore_result = restored.get("result")
        if not isinstance(restore_result, dict) or restore_result.get("ready") is not True:
            raise DrillFailure("backup_restore_failed")
        purge_replay = restore_result.get("purge_replay")
        if not isinstance(purge_replay, dict) or purge_replay.get("pending_count") != 0:
            raise DrillFailure("restore_purge_replay_failed")
        with target_engine.connect() as connection:
            removed_rows = int(
                connection.execute(
                    select(func.count())
                    .select_from(observations)
                    .where(observations.c.post_id == removed.post_id)
                ).scalar_one()
            )
            kept_rows = int(
                connection.execute(
                    select(func.count())
                    .select_from(observations)
                    .where(observations.c.post_id == kept.post_id)
                ).scalar_one()
            )
        if removed_rows != 0 or kept_rows != 1:
            raise DrillFailure("restored_scope_state_invalid")

        restored_media = paths.root / "restored-media"
        for ids, readable in ((removed, False), (kept, True)):
            context_env = self._env(target_url, paths, ids.group_key)
            context_env["PAIYA_FIXTURE_MEDIA_ROOT"] = str(restored_media)
            port = self._start_serve(context_env, paths.root / f"serve-{ids.group_key}.log")
            self._http_scope_checks(port, ids, readable=readable)
            self._stop_all_processes()
            self._run_mcp(context_env, ids)
        return {
            "backup_restore_ready": True,
            "purge_replay_pending": 0,
            "removed_grant_api_tools_hidden": True,
            "kept_grant_api_tools_readable": True,
            "mcp_tools_checked_per_scope": 5,
        }

    def _step(self, name: str, action: Any) -> None:
        try:
            facts = action()
            if not isinstance(facts, dict):
                raise DrillFailure("step_result_invalid")
            self.steps.append({"name": name, "passed": True, **facts})
        except DrillFailure as error:
            self.steps.append({"name": name, "passed": False, "error": {"code": error.code}})
            raise
        except Exception as error:
            code = "step_timeout" if isinstance(error, subprocess.TimeoutExpired) else "step_failed"
            self.steps.append({"name": name, "passed": False, "error": {"code": code}})
            raise DrillFailure(code) from error

    def run(self) -> int:
        try:
            with self.admin_engine.connect() as connection:
                version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
            if version // 10000 != 17:
                raise DrillFailure("postgresql_17_required")
            self._step(
                "after_media_write",
                lambda: self._run_crash_step("after_media_write"),
            )
            self._step(
                "after_ingest_commit",
                lambda: self._run_crash_step("after_ingest_commit"),
            )
            self._step(
                "before_job_complete",
                lambda: self._run_crash_step("before_job_complete"),
            )
            self._step("sleep_missed_slots", self._run_sleep_step)
            self._step("restore_api_and_mcp", self._run_restore_step)
        except DrillFailure:
            pass
        finally:
            cleanup = self.cleanup()
        report = {
            "ok": len(self.steps) == 5
            and all(step.get("passed") is True for step in self.steps)
            and cleanup["passed"],
            "steps": self.steps,
            "cleanup": cleanup,
        }
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")), flush=True)
        return 0 if report["ok"] else 1

    def cleanup(self) -> dict[str, object]:
        errors: list[str] = []
        try:
            self._stop_all_processes()
        except Exception:
            errors.append("process_stop_failed")
        for engine in reversed(self.engines):
            try:
                engine.dispose()
            except Exception:
                errors.append("database_engine_dispose_failed")
        dropped = 0
        try:
            with self.admin_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                if self.database_names:
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = ANY(:names) AND pid <> pg_backend_pid()"
                        ),
                        {"names": self.database_names},
                    )
                for name in reversed(self.database_names):
                    try:
                        connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                        dropped += 1
                    except SQLAlchemyError:
                        errors.append("database_drop_failed")
        except SQLAlchemyError:
            errors.append("database_cleanup_failed")
        self.admin_engine.dispose()
        root_removed = False
        try:
            shutil.rmtree(self.root)
            root_removed = not self.root.exists()
        except OSError:
            errors.append("temporary_directory_cleanup_failed")
        return {
            "passed": not errors and dropped == len(self.database_names) and root_removed,
            "drill_databases_dropped": dropped,
            "temporary_directories_removed": root_removed,
            "processes_stopped": True,
            "errors": errors,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=_HELP)
    parser.parse_args()
    raw_url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    if not raw_url:
        report = {
            "ok": False,
            "steps": [],
            "cleanup": {"passed": True, "drill_databases_dropped": 0},
            "error": {"code": "test_database_required"},
        }
        print(json.dumps(report, separators=(",", ":")))
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="paiya-o4-continuity-") as directory:
            drill = ContinuityDrill(Path(directory), make_url(raw_url))
            return drill.run()
    except DrillFailure as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "steps": [],
                    "cleanup": {"passed": True, "drill_databases_dropped": 0},
                    "error": {"code": error.code},
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
