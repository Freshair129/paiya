"""Synthetic acceptance for the local Collector inbox."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine, func, select, update
from test_a2_ingest import pg_engine as _pg_engine
from test_i1_inbox_validate import fixture_run_dir

from paiya.cli import main
from paiya.inbox.validation import validate_inbox
from paiya.store.tables import capture_runs, media_assets, observations, source_grants

pg_engine = _pg_engine


def test_ingest_requires_an_explicit_run_id(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["inbox", "ingest"])
    assert error.value.code == 2
    assert "run_id" in json.loads(capsys.readouterr().out)["error"]["message"]


@pytest.fixture
def collector_db(pg_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:  # noqa: F811
    monkeypatch.setattr("paiya.cli.get_database_engine", lambda: pg_engine)
    return pg_engine


def _call(capsys: pytest.CaptureFixture[str], *args: str, expected: int = 0) -> dict:
    code = main(list(args))
    result = json.loads(capsys.readouterr().out)
    assert code == expected, result
    return result


@pytest.fixture
def inbox_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    inbox, media = tmp_path / "inbox", tmp_path / "media"
    (inbox / "incoming").mkdir(parents=True)
    media.mkdir()
    monkeypatch.setenv("PAIYA_COLLECTOR_INBOX", str(inbox))
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(media))
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(tmp_path / "purge.jsonl"))
    monkeypatch.setenv("PAIYA_DISK_MIN_FREE_BYTES", "1")
    return inbox, media


def _publish(inbox: Path, grant_id: UUID, started: datetime) -> Path:
    started = started.replace(microsecond=0)
    run_id = uuid4()
    path = inbox / "incoming" / str(run_id)
    source = fixture_run_dir("valid-complete")
    shutil.copytree(source, path)
    run_path = path / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run.update(
        run_id=str(run_id),
        grant_id=str(grant_id),
        group_key="owner-group-01",
        started_at=started.isoformat(),
        ended_at=(started + timedelta(seconds=1)).isoformat(),
    )
    run_path.write_text(json.dumps(run), encoding="utf-8")
    for envelope in (path / "envelopes").glob("*.json"):
        doc = json.loads(envelope.read_text(encoding="utf-8"))
        payload = doc["payload"]
        payload.update(
            group_key="owner-group-01", observed_at=started.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        seller = payload.get("seller")
        if seller is not None:
            seller["identity_scope"] = "owner-group-01"
        capture_id = str(uuid5(run_id, payload["source_post_key"]))
        doc.update(run_id=str(run_id), grant_id=str(grant_id), capture_id=capture_id)
        envelope.unlink()
        envelope.with_name(f"{capture_id}.json").write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_synthetic_bundle_matches_i1_contract(inbox_setup: tuple[Path, Path]) -> None:
    inbox, _media = inbox_setup
    path = _publish(inbox, uuid4(), datetime.now(UTC) - timedelta(minutes=1))
    report = validate_inbox(path)
    assert report["ok"] is True, report


def _grant(capsys: pytest.CaptureFixture[str]) -> UUID:
    result = _call(capsys, "grant", "create-collector", "--authority-ref", "synthetic I2 test")
    return UUID(result["result"]["grant_id"])


def test_ingests_image_and_rejects_duplicate_run(
    collector_db: Engine, inbox_setup: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    inbox, media = inbox_setup
    grant_id = _grant(capsys)
    with collector_db.begin() as conn:
        conn.execute(
            update(source_grants).values(valid_from=datetime.now(UTC) - timedelta(hours=1))
        )
    run = _publish(inbox, grant_id, datetime.now(UTC) - timedelta(minutes=1))
    duplicate = inbox / "duplicate"
    shutil.copytree(run, duplicate)
    result = _call(capsys, "inbox", "ingest", run.name)
    assert result["result"]["state"] == "done"
    result_file = inbox / "done" / run.name / "result.json"
    assert result_file.is_file()
    assert sorted(p.name for p in result_file.parent.iterdir()) == ["result.json"]
    with collector_db.connect() as conn:
        assert conn.execute(select(capture_runs.c.coverage)).scalar_one() == "complete"
        assert conn.execute(select(observations.c.grant_id)).scalar_one() == grant_id
        assert conn.execute(select(func.count()).select_from(media_assets)).scalar_one() == 1
    assert len(list((media / "assets").iterdir())) == 1
    duplicate.rename(run)
    duplicate_result = _call(capsys, "inbox", "ingest", run.name, expected=1)
    assert duplicate_result["result"]["code"] == "run_duplicate"


@pytest.mark.parametrize("denial", ["revoked", "missing_media", "expired", "invalid_bundle"])
def test_rejects_whole_run_without_database_writes(
    collector_db: Engine,
    inbox_setup: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    denial: str,
) -> None:
    inbox, _media = inbox_setup
    grant_id = _grant(capsys)
    now = datetime.now(UTC)
    with collector_db.begin() as conn:
        values: dict = {"valid_from": now - timedelta(days=9)}
        if denial == "revoked":
            values["revoked_at"] = now
        elif denial == "missing_media":
            values["scopes"] = ["capture", "read"]
        conn.execute(update(source_grants).values(**values))
    started = now - (timedelta(days=8) if denial == "expired" else timedelta(minutes=1))
    run = _publish(inbox, grant_id, started)
    if denial == "invalid_bundle":
        next((run / "assets").iterdir()).write_bytes(b"not an image")
    result = _call(capsys, "inbox", "ingest", run.name, expected=1)["result"]
    assert (
        result["code"]
        == {
            "revoked": "grant_unavailable",
            "missing_media": "grant_unavailable",
            "expired": "run_expired",
            "invalid_bundle": "validation_failed",
        }[denial]
    )
    assert not run.exists()
    with collector_db.connect() as conn:
        assert conn.execute(select(func.count()).select_from(capture_runs)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(observations)).scalar_one() == 0


def test_prune_expired_run_preserves_newer_observation_and_shared_media(
    collector_db: Engine,
    inbox_setup: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbox, media = inbox_setup
    grant_id = _grant(capsys)
    now = datetime.now(UTC)
    with collector_db.begin() as conn:
        conn.execute(update(source_grants).values(valid_from=now - timedelta(days=9)))
    old = _publish(inbox, grant_id, now - timedelta(days=6))
    fresh = _publish(inbox, grant_id, now - timedelta(minutes=1))
    _call(capsys, "inbox", "ingest", old.name)
    _call(capsys, "inbox", "ingest", fresh.name)
    with collector_db.begin() as conn:
        conn.execute(
            update(capture_runs)
            .where(capture_runs.c.id == UUID(old.name))
            .values(started_at=now - timedelta(days=8))
        )
    expired_pending = _publish(inbox, grant_id, now - timedelta(days=8))
    result = _call(capsys, "inbox", "prune-expired")["result"]
    assert result["state"] == "succeeded"
    assert result["expired_bundles"] == 1
    assert not expired_pending.exists()
    with collector_db.connect() as conn:
        assert conn.execute(select(observations.c.run_id)).scalar_one() == UUID(fresh.name)
        assert conn.execute(select(capture_runs.c.id)).scalar_one() == UUID(fresh.name)
    assert len(list((media / "assets").iterdir())) == 1
    assert _call(capsys, "purge", "replay")["pending_count"] == 0
