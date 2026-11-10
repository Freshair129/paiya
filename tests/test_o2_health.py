"""O2 health, low-disk ingest, and JSON event acceptance tests."""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine
from test_a2_ingest import (
    WEBP,
)
from test_a2_ingest import (
    document as document,
)
from test_a2_ingest import (
    pg_engine as pg_engine,
)
from test_a2_ingest import (
    scope as scope,
)

from paiya import cli
from paiya.api import evidence as evidence_api
from paiya.api import health as health_api
from paiya.api.app import app
from paiya.health.service import get_health_report
from paiya.ingest.service import ingest_fixture
from paiya.logging import emit_event, safe_token
from paiya.store.fixture_scope import FixtureScope
from paiya.store.media import media_path
from paiya.store.tables import (
    backup_runs,
    capture_gaps,
    capture_runs,
    current_items,
    evidence,
    jobs,
    observations,
    status_facts,
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _new_image(data: bytes) -> dict[str, object]:
    return {
        "staging_key": "health-image",
        "sha256": hashlib.sha256(data).hexdigest(),
        "mime": "image/webp",
        "width": 2,
        "height": 2,
        "bytes": len(data),
    }


def _new_document(document: dict, *, now: datetime, label: str) -> dict:
    updated = copy.deepcopy(document)
    updated["run_id"] = str(uuid4())
    updated["capture_id"] = f"health-{uuid4()}"
    updated["payload"]["source_post_key"] = f"health-{label}-{uuid4().hex}"
    updated["payload"]["permalink"] = f"https://example.invalid/{label}-canary"
    updated["payload"]["observed_at"] = _iso(now)
    return updated


def _attach_image(document: dict, data: bytes) -> dict[str, bytes]:
    document["assets"] = {"s01-open": _new_image(data)}
    document["payload"]["evidence"][0]["media_state"] = "captured"
    return {"health-image": data}


def _insert_job(
    conn,
    scope: FixtureScope,
    *,
    kind: str,
    state: str,
    due_at: datetime,
) -> object:
    job_id = uuid4()
    conn.execute(
        insert(jobs).values(
            id=job_id,
            group_id=scope.group_id,
            grant_id=scope.grant_id,
            kind=kind,
            target_key=f"health:{job_id}",
            logical_key=f"health:{job_id}",
            due_at=due_at,
            state=state,
            retry_count=0,
        )
    )
    return job_id


def test_o2_low_disk_keeps_observation_and_facts_without_writing_media(
    pg_engine: Engine,
    scope: FixtureScope,
    document: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "10")
    document = _new_document(document, now=datetime.now(UTC), label="secret")
    document["payload"]["evidence"][0]["source_span"]["quote"] = "O2 fixture text canary"
    assets = _attach_image(document, WEBP)
    digest = str(document["assets"]["s01-open"]["sha256"])

    observation_id = ingest_fixture(
        pg_engine,
        scope,
        document,
        assets,
        tmp_path,
        disk_usage_fn=lambda _path: (1_000, 991, 9),
    )

    assert not media_path(tmp_path, digest, "image/webp").exists()
    with pg_engine.connect() as conn:
        stored_observation = conn.execute(
            select(observations.c.id).where(observations.c.id == observation_id)
        ).scalar_one_or_none()
        stored_evidence = (
            conn.execute(
                select(
                    evidence.c.media_state, evidence.c.media_reason, evidence.c.media_asset_id
                ).where(evidence.c.observation_id == observation_id)
            )
            .mappings()
            .one()
        )
        fact_count = conn.execute(
            select(func.count())
            .select_from(status_facts)
            .where(status_facts.c.observation_id == observation_id)
        ).scalar_one()
        current_count = conn.execute(
            select(func.count())
            .select_from(current_items)
            .where(current_items.c.last_observation_id == observation_id)
        ).scalar_one()

    assert stored_observation == observation_id
    assert stored_evidence == {
        "media_state": "capture_failed",
        "media_reason": "disk_low",
        "media_asset_id": None,
    }
    assert fact_count > 0
    assert current_count == 1

    stderr = capsys.readouterr().err
    events = [json.loads(line) for line in stderr.splitlines()]
    assert [event["event"] for event in events] == ["disk_low", "media_failed"]
    assert events[0]["free_bytes"] == 9
    assert events[1]["reason"] == "disk_low"
    assert "secret-canary" not in stderr
    assert "example.invalid" not in stderr
    assert "O2 fixture text canary" not in stderr


def test_o2_existing_media_hash_links_without_checking_disk(
    pg_engine: Engine,
    scope: FixtureScope,
    document: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "10")
    first_doc = _new_document(document, now=datetime.now(UTC), label="first")
    first_observation_id = ingest_fixture(
        pg_engine,
        scope,
        first_doc,
        _attach_image(first_doc, WEBP),
        tmp_path,
        disk_usage_fn=lambda _path: (1_000, 800, 200),
    )
    second_doc = _new_document(document, now=datetime.now(UTC), label="second")

    def unexpected_disk_measurement(_path: Path) -> tuple[int, int, int]:
        raise AssertionError("disk was measured for an existing media hash")

    second_observation_id = ingest_fixture(
        pg_engine,
        scope,
        second_doc,
        _attach_image(second_doc, WEBP),
        tmp_path,
        disk_usage_fn=unexpected_disk_measurement,
    )

    with pg_engine.connect() as conn:
        linked_assets = conn.execute(
            select(evidence.c.media_asset_id, evidence.c.media_state)
            .where(evidence.c.observation_id.in_([first_observation_id, second_observation_id]))
            .order_by(evidence.c.observation_id)
        ).all()

    assert len(linked_assets) == 2
    assert linked_assets[0].media_state == linked_assets[1].media_state == "captured"
    assert linked_assets[0].media_asset_id == linked_assets[1].media_asset_id


def test_o2_health_reports_overdue_coverage_media_backlog_and_real_backup_time(
    pg_engine: Engine,
    scope: FixtureScope,
    document: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    media_root = tmp_path / "media"
    backup_root = tmp_path / "backups"
    media_root.mkdir()
    backup_root.mkdir()
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("PAIYA_BACKUP_DIR", str(backup_root))
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "100")
    monkeypatch.setenv("PAIYA_JOB_OVERDUE_MINUTES", "23")

    captured_doc = _new_document(document, now=now - timedelta(minutes=5), label="captured")
    ingest_fixture(
        pg_engine,
        scope,
        captured_doc,
        _attach_image(captured_doc, WEBP),
        media_root,
        disk_usage_fn=lambda _path: (1_000, 800, 200),
    )
    failed_image = bytearray(WEBP)
    failed_image[-1] ^= 1
    failed_doc = _new_document(document, now=now - timedelta(minutes=4), label="failed")
    ingest_fixture(
        pg_engine,
        scope,
        failed_doc,
        _attach_image(failed_doc, bytes(failed_image)),
        media_root,
        disk_usage_fn=lambda _path: (1_000, 901, 99),
    )
    not_requested = _new_document(document, now=now - timedelta(minutes=3), label="no-media")
    ingest_fixture(pg_engine, scope, not_requested, {}, media_root)

    with pg_engine.begin() as conn:
        _insert_job(conn, scope, kind="revisit", state="queued", due_at=now - timedelta(minutes=23))
        _insert_job(conn, scope, kind="revisit", state="queued", due_at=now - timedelta(minutes=20))
        _insert_job(
            conn,
            scope,
            kind="enrich",
            state="queued",
            due_at=now - timedelta(minutes=30),
        )
        _insert_job(
            conn,
            scope,
            kind="enrich",
            state="failed",
            due_at=now - timedelta(hours=1),
        )
        _insert_job(
            conn,
            scope,
            kind="revisit",
            state="queued",
            due_at=now - timedelta(minutes=5),
        )
        run_job_id = _insert_job(
            conn,
            scope,
            kind="discover",
            state="succeeded",
            due_at=now - timedelta(hours=3),
        )
        run_id = uuid4()
        run_started = now - timedelta(hours=3)
        run_ended = now - timedelta(hours=2, minutes=55)
        conn.execute(
            insert(capture_runs).values(
                id=run_id,
                job_id=run_job_id,
                group_id=scope.group_id,
                grant_id=scope.grant_id,
                source_kind="fixture",
                started_at=run_started,
                ended_at=run_ended,
                session_result="ok",
                sort_result="verified_latest",
                coverage="partial",
                partial_reasons=["sort_unverified"],
                stop_reason="feed_end",
                cards_seen=3,
                cards_parsed=3,
                scroll_steps=0,
                error_code=None,
            )
        )
        conn.execute(
            insert(capture_gaps).values(
                id=uuid4(),
                group_id=scope.group_id,
                gap_from=now - timedelta(days=1, minutes=30),
                gap_to=now - timedelta(days=1),
                reason="run_failed",
                detected_at=now - timedelta(hours=20),
                compensated_by_job_id=None,
            )
        )
        last_backup = now - timedelta(hours=27)
        conn.execute(
            insert(backup_runs).values(
                id=uuid4(),
                started_at=last_backup - timedelta(minutes=5),
                completed_at=last_backup,
                state="succeeded",
                db_bytes=100,
                media_count=1,
                media_bytes=25,
                set_name="synthetic-backup",
            )
        )
        conn.execute(
            insert(backup_runs).values(
                id=uuid4(),
                started_at=now - timedelta(hours=2),
                completed_at=now - timedelta(hours=1),
                state="failed",
                error_code="backup_failed",
            )
        )

    def disk_usage(path: Path) -> tuple[int, int, int]:
        if path.name == "backups":
            return (1_000, 950, 50)
        return (1_000, 950, 50)

    report = get_health_report(pg_engine, now=now, disk_usage_fn=disk_usage)
    queue = report["queue"]
    coverage = report["coverage"]
    media = report["media"]
    backup = report["backup"]
    backlog = report["ai_backlog"]

    assert queue["enrich"] == {
        "overdue_count": 1,
        "oldest_overdue_age_minutes": 30,
    }
    assert queue["revisit"] == {
        "overdue_count": 0,
        "oldest_overdue_age_minutes": None,
    }
    group_health = next(row for row in coverage if row["group_id"] == str(scope.group_id))
    assert group_health["last_successful_at"] == _iso(run_ended)
    assert group_health["latest_coverage"] == "partial"
    assert group_health["capture_gap_count"] == 1
    assert group_health["latest_gap_7d"]["reason"] == "run_failed"
    assert media == {
        "captured": 1,
        "capture_failed": 1,
        "success_rate": 0.5,
        "disk_low": 1,
    }
    assert report["disk"]["media"]["state"] == "low"
    assert report["disk"]["backup"]["state"] == "low"
    assert backup["state"] == "stale"
    assert datetime.fromisoformat(backup["last_success_at"].replace("Z", "+00:00")) == last_backup
    assert backup["age_hours"] == 27.0
    assert datetime.fromisoformat(
        backup["last_failure_at"].replace("Z", "+00:00")
    ) == now - timedelta(hours=1)
    assert backlog["states"]["queued"] == 1
    assert backlog["states"]["failed"] == 1
    assert backlog["oldest_queued_age_seconds"] == 1_800
    assert report["checked_at"] == _iso(now)
    disk_events = [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if json.loads(line)["event"] == "disk_low"
    ]
    assert {event["path_kind"] for event in disk_events} == {"media", "backup"}


def test_o2_gap_window_includes_seven_days_but_excludes_older(
    pg_engine: Engine, scope: FixtureScope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    monkeypatch.delenv("PAIYA_BACKUP_DIR", raising=False)
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "1")
    with pg_engine.begin() as conn:
        for detected_at in (now - timedelta(days=7, seconds=1), now - timedelta(days=7)):
            conn.execute(
                insert(capture_gaps).values(
                    id=uuid4(),
                    group_id=scope.group_id,
                    gap_from=detected_at - timedelta(hours=1),
                    gap_to=detected_at,
                    reason="run_failed",
                    detected_at=detected_at,
                    compensated_by_job_id=None,
                )
            )
    report = get_health_report(pg_engine, now=now, disk_usage_fn=lambda _path: (1000, 500, 500))
    group = next(row for row in report["coverage"] if row["group_id"] == str(scope.group_id))
    assert group["capture_gap_count"] == 2
    assert group["latest_gap_7d"]["detected_at"] == _iso(now - timedelta(days=7))
    later = get_health_report(
        pg_engine, now=now + timedelta(seconds=1), disk_usage_fn=lambda _path: (1000, 500, 500)
    )
    assert (
        next(row for row in later["coverage"] if row["group_id"] == str(scope.group_id))[
            "latest_gap_7d"
        ]
        is None
    )


def test_o2_backup_at_26_hours_is_not_stale(
    pg_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    completed_at = now - timedelta(hours=26)
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    monkeypatch.delenv("PAIYA_BACKUP_DIR", raising=False)
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "1")
    with pg_engine.begin() as conn:
        conn.execute(
            insert(backup_runs).values(
                id=uuid4(),
                started_at=completed_at - timedelta(hours=1),
                completed_at=completed_at,
                state="succeeded",
            )
        )
    report = get_health_report(pg_engine, now=now, disk_usage_fn=lambda _path: (1000, 500, 500))
    assert report["backup"]["state"] == "ok"
    assert report["backup"]["age_hours"] == 26
    assert (
        datetime.fromisoformat(report["backup"]["last_success_at"].replace("Z", "+00:00"))
        == completed_at
    )


def test_o2_zero_media_denominator_and_never_backed_up(
    pg_engine: Engine,
    scope: FixtureScope,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    monkeypatch.delenv("PAIYA_BACKUP_DIR", raising=False)
    report = get_health_report(
        pg_engine,
        now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
        disk_usage_fn=lambda _path: (1_000, 500, 500),
    )

    assert report["media"] == {
        "captured": 0,
        "capture_failed": 0,
        "success_rate": None,
        "disk_low": 0,
    }
    assert report["backup"]["state"] == "never"
    assert report["backup"]["last_success_at"] is None
    assert "backup" not in report["disk"]


def test_o2_backup_age_just_over_26_hours_is_stale(
    pg_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    completed_at = now - timedelta(hours=26, seconds=1)
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    monkeypatch.delenv("PAIYA_BACKUP_DIR", raising=False)
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "1")
    with pg_engine.begin() as conn:
        conn.execute(
            insert(backup_runs).values(
                id=uuid4(),
                started_at=completed_at - timedelta(minutes=5),
                completed_at=completed_at,
                state="succeeded",
            )
        )

    report = get_health_report(
        pg_engine,
        now=now,
        disk_usage_fn=lambda _path: (1_000, 500, 500),
    )

    assert report["backup"]["state"] == "stale"
    assert report["backup"]["age_hours"] > 26


def test_o2_admin_endpoint_uses_existing_read_context_rule(
    pg_engine: Engine,
    scope: FixtureScope,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    monkeypatch.delenv("PAIYA_BACKUP_DIR", raising=False)
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "1")
    monkeypatch.setattr(evidence_api, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(health_api, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(
        health_api,
        "get_health_report",
        lambda engine: get_health_report(engine, disk_usage_fn=lambda _path: (1_000, 500, 500)),
    )

    with TestClient(app) as client:
        monkeypatch.delenv("PAIYA_READ_CONTEXT_GROUP_KEY", raising=False)
        assert client.get("/v1/admin/health").status_code == 401

        monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-missing")
        assert client.get("/v1/admin/health").status_code == 403

        monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", scope.group_key)
        response = client.get("/v1/admin/health")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "checked_at" in response.json()
    assert "ai_backlog" in response.json()


def test_o2_cli_prints_the_same_raw_health_shape(
    pg_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    monkeypatch.delenv("PAIYA_BACKUP_DIR", raising=False)
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "1")
    monkeypatch.setattr(cli, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(
        cli,
        "get_health_report",
        lambda engine: get_health_report(engine, disk_usage_fn=lambda _path: (1_000, 500, 500)),
    )

    assert cli.main(["health"]) == 0
    output = capsys.readouterr().out.strip()
    report = json.loads(output)
    assert set(report) == {
        "checked_at",
        "queue",
        "coverage",
        "media",
        "disk",
        "backup",
        "ai_backlog",
    }
    assert not isinstance(report.get("result"), dict)


def test_o2_logger_emits_each_contract_event_as_json_without_payload_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = str(uuid4())
    group_id = str(uuid4())
    job_id = str(uuid4())
    backup_id = str(uuid4())
    purge_id = str(uuid4())
    emit_event(
        "capture_run_finished",
        run_id=run_id,
        group_id=group_id,
        job_kind="discover",
        adapter_version="fixture-v1",
        result="complete",
        reason="feed_end",
        duration_ms=12,
        queue_wait_ms=0,
        cards_seen=2,
        cards_parsed=2,
        duplicate_cards_skipped=0,
    )
    emit_event(
        "media_failed",
        run_id=run_id,
        evidence_local_key=safe_token("synthetic-evidence"),
        reason="disk_low",
    )
    emit_event("disk_low", path_kind="media", free_bytes=9, min_free_bytes=10)
    emit_event(
        "enrich_job_finished",
        job_id=job_id,
        result="succeeded",
        reason=None,
        duration_ms=8,
    )
    emit_event(
        "backup_run_finished",
        backup_run_id=backup_id,
        result="failed",
        reason="backup_failed",
        duration_ms=21,
    )
    emit_event("purge_finished", purge_id=purge_id, result="succeeded", pending_count=0)

    captured = capsys.readouterr()
    assert captured.out == ""
    records = [json.loads(line) for line in captured.err.splitlines()]
    assert [record["event"] for record in records] == [
        "capture_run_finished",
        "media_failed",
        "disk_low",
        "enrich_job_finished",
        "backup_run_finished",
        "purge_finished",
    ]
    required = {
        "capture_run_finished": {
            "run_id",
            "group_id",
            "job_kind",
            "adapter_version",
            "result",
            "reason",
            "duration_ms",
            "queue_wait_ms",
            "cards_seen",
            "cards_parsed",
            "duplicate_cards_skipped",
        },
        "media_failed": {"run_id", "evidence_local_key", "reason"},
        "disk_low": {"path_kind", "free_bytes", "min_free_bytes"},
        "enrich_job_finished": {"job_id", "result", "reason", "duration_ms"},
        "backup_run_finished": {"backup_run_id", "result", "reason", "duration_ms"},
        "purge_finished": {"purge_id", "result", "pending_count"},
    }
    for record in records:
        assert set(record) == {"ts", "level", "event"} | required[record["event"]]
        assert record["ts"].endswith("Z")
        assert datetime.fromisoformat(
            record["ts"].replace("Z", "+00:00")
        ).utcoffset() == UTC.utcoffset(datetime.now(UTC))
        assert record["level"] in {"info", "warning"}
    assert "synthetic-evidence" in captured.err
    assert "permalink" not in captured.err
    assert "secret" not in captured.err


def test_revisit_completion_log_reports_no_discovery_duplicates(capsys) -> None:
    from time import perf_counter

    from paiya.collector.revisit_run import _log_capture_finished
    from paiya.scheduler.models import JobClaim

    now = datetime.now(UTC)
    claim = JobClaim(
        job_id=uuid4(),
        group_id=uuid4(),
        grant_id=uuid4(),
        run_id=uuid4(),
        kind="revisit",
        logical_key="revisit:synthetic-post",
        target_key="synthetic-post",
        due_at=now,
        lease_token=uuid4(),
        lease_until=now + timedelta(minutes=5),
    )

    _log_capture_finished(
        claim,
        started_at=now,
        started_clock=perf_counter(),
        result="none",
        reason="post_unavailable",
        cards_seen=1,
        cards_parsed=0,
    )

    event = json.loads(capsys.readouterr().err)
    assert event["event"] == "capture_run_finished"
    assert event["job_kind"] == "revisit"
    assert event["duplicate_cards_skipped"] == 0
