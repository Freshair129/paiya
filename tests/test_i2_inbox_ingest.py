"""Synthetic acceptance for the local Collector inbox."""

import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine, event, func, select, update
from test_a2_ingest import pg_engine as _pg_engine
from test_i1_inbox_validate import fixture_run_dir

from paiya.cli import main
from paiya.grants.service import revoke_grant
from paiya.inbox import service as inbox_service
from paiya.inbox.validation import validate_inbox
from paiya.read.context import resolve_readable_grants
from paiya.store.tables import (
    capture_runs,
    comments,
    media_assets,
    observations,
    posts,
    sellers,
    source_grants,
)

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
    (tmp_path / "purge").mkdir()
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(tmp_path / "purge" / "purge.jsonl"))
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
    with pytest.MonkeyPatch.context() as env:

        def unexpected_claim(*args, **kwargs):
            raise AssertionError("A duplicate must be rejected before filesystem claim")

        env.setattr(Path, "rename", unexpected_claim)
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


def _put_comments(run: Path, entries: list[dict]) -> None:
    envelope = next((run / "envelopes").glob("*.json"))
    document = json.loads(envelope.read_text(encoding="utf-8"))
    payload = document["payload"]
    payload["comments"] = entries
    payload["activity"]["distinct_comments_seen"] = len(entries)
    envelope.write_text(json.dumps(document), encoding="utf-8")
    report = validate_inbox(run)
    assert report["ok"], report


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
    envelope = next((old / "envelopes").glob("*.json"))
    document = json.loads(envelope.read_text(encoding="utf-8"))
    document["payload"]["seller"] = {
        "display_name": "Synthetic seller",
        "external_actor_key": "synthetic-seller",
        "identity_scope": "owner-group-01",
    }
    document["payload"]["posted_at"] = {
        "source_text": "synthetic date",
        "value": (now - timedelta(days=6, hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "precision": "exact",
    }
    envelope.write_text(json.dumps(document), encoding="utf-8")
    child = {
        "source_comment_key": "child",
        "actor_key": None,
        "role": "unknown",
        "text": "Synthetic reply",
        "source_time": {"source_text": None, "value": None, "precision": "unknown"},
    }
    _put_comments(
        old, [dict(child, source_comment_key="old-parent"), dict(child, parent_key="old-parent")]
    )
    _put_comments(fresh, [child])
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
        assert conn.execute(select(posts.c.seller_id, posts.c.posted_at)).one() == (None, None)
        assert conn.execute(select(func.count()).select_from(sellers)).scalar_one() == 0
        assert conn.execute(select(comments.c.source_comment_key, comments.c.parent_id)).one() == (
            "child",
            None,
        )
    assert len(list((media / "assets").iterdir())) == 1
    assert _call(capsys, "purge", "replay")["pending_count"] == 0


def test_inbox_cannot_live_under_purge_log_directory(
    inbox_setup: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox, _ = inbox_setup
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(inbox.parent / "purge.jsonl"))
    with pytest.raises(inbox_service.InboxError, match="separate from"):
        inbox_service.inbox_paths()


@pytest.mark.parametrize("damage", ["image", "summary", "ready"])
def test_expiry_removes_damaged_claim(
    collector_db: Engine,
    inbox_setup: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    damage: str,
) -> None:
    inbox, _ = inbox_setup
    started = datetime.now(UTC) - timedelta(days=8)
    run = _publish(inbox, uuid4(), started)
    claimed = inbox / "processing" / run.name
    claimed.parent.mkdir()
    run.rename(claimed)
    if damage == "image":
        next((claimed / "assets").iterdir()).write_bytes(b"damaged evidence")
    elif damage == "summary":
        (claimed / "run.json").write_bytes(b"{")
    else:
        (claimed / "run.json").unlink()
        (claimed / "READY").unlink()
    if damage == "ready":
        for file in (claimed / "assets").iterdir():
            os.utime(file, (started.timestamp(), started.timestamp()))
    if (claimed / "READY").exists():
        os.utime(claimed / "READY", (started.timestamp(), started.timestamp()))
    newer = _publish(inbox, uuid4(), datetime.now(UTC) - timedelta(minutes=1))
    result = _call(capsys, "inbox", "prune-expired")["result"]
    assert result["expired_bundles"] == 1
    assert not claimed.exists()
    assert newer.is_dir()


@pytest.mark.parametrize("failure", ["grant_revoked", "interrupted"])
def test_failed_ingest_recovers_media(
    collector_db: Engine,
    inbox_setup: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    inbox, media = inbox_setup
    grant_id = _grant(capsys)
    now = datetime.now(UTC)
    with collector_db.begin() as conn:
        conn.execute(update(source_grants).values(valid_from=now - timedelta(days=9)))
    run = _publish(inbox, grant_id, now - timedelta(minutes=1))
    verify = inbox_service.verify_collector_grant

    def late_failure(*args, **kwargs):
        if kwargs.get("lock"):
            assert len(list((media / "assets").glob("*.webp"))) == 1
            if failure == "grant_revoked":
                limited_engine = collector_db.execution_options(isolation_level="READ COMMITTED")

                @event.listens_for(limited_engine, "begin")
                def limit_wait(conn):
                    conn.exec_driver_sql("SET LOCAL statement_timeout = '5s'")

                revoke_grant(limited_engine, grant_id)
            else:
                raise KeyboardInterrupt
        return verify(*args, **kwargs)

    with monkeypatch.context() as env:
        env.setattr(inbox_service, "verify_collector_grant", late_failure)
        if failure == "grant_revoked":
            assert _call(capsys, "inbox", "ingest", run.name, expected=1)["result"]["code"] == (
                "grant_unavailable"
            )
        else:
            _call(capsys, "inbox", "ingest", run.name)
            claimed = inbox / "processing" / run.name
            manifest = claimed / "run.json"
            summary = json.loads(manifest.read_text(encoding="utf-8"))
            summary["started_at"] = (now - timedelta(days=8)).isoformat()
            manifest.write_text(json.dumps(summary), encoding="utf-8")
            assert _call(capsys, "inbox", "prune-expired")["result"]["expired_bundles"] == 1
    assert not list((media / "assets").iterdir())
    with collector_db.connect() as conn:
        assert conn.execute(select(func.count()).select_from(media_assets)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(observations)).scalar_one() == 0


def test_rejection_receipt_does_not_retain_untrusted_names(
    collector_db: Engine, inbox_setup: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    inbox, _ = inbox_setup
    run = _publish(inbox, uuid4(), datetime.now(UTC) - timedelta(minutes=1))
    (run / "synthetic-private-post-text.txt").write_text("synthetic", encoding="utf-8")
    rejected = _call(capsys, "inbox", "ingest", run.name, expected=1)["result"]
    assert "layout_unexpected" in rejected["problem_codes"]
    receipt = (inbox / "rejected" / run.name / "result.json").read_text(encoding="utf-8")
    assert "synthetic-private-post-text" not in receipt


def test_revoked_grant_purge_removes_pending_and_unblocks_other_grant(
    collector_db: Engine, inbox_setup: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    inbox, _ = inbox_setup
    grant = _grant(capsys)
    other_grant = _grant(capsys)
    now = datetime.now(UTC)
    with collector_db.begin() as conn:
        conn.execute(update(source_grants).values(valid_from=now - timedelta(days=9)))
    completed = _publish(inbox, grant, now - timedelta(minutes=1))
    _call(capsys, "inbox", "ingest", completed.name)
    incoming = _publish(inbox, grant, now - timedelta(minutes=1))
    claimed = _publish(inbox, grant, now - timedelta(minutes=1))
    (inbox / "processing").mkdir(exist_ok=True)
    claimed = claimed.rename(inbox / "processing" / claimed.name)
    fresh = _publish(inbox, other_grant, now - timedelta(minutes=1))

    with collector_db.connect() as conn:
        assert len(resolve_readable_grants(conn)) == 2
    _call(capsys, "grant", "revoke", str(grant))
    with collector_db.connect() as conn:
        assert resolve_readable_grants(conn) == []
    _call(capsys, "purge", "run", "--scope", f"grant:{grant}")
    assert not incoming.exists()
    assert not claimed.exists()
    assert fresh.is_dir()
    with collector_db.connect() as conn:
        assert [row.grant_id for row in resolve_readable_grants(conn)] == [other_grant]


def test_prune_leaves_foreign_and_unpublished_uuid_directories(
    collector_db: Engine, inbox_setup: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    inbox, _ = inbox_setup
    started = datetime.now(UTC) - timedelta(days=8)
    foreign = _publish(inbox, uuid4(), started)
    path = foreign / "run.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["group_key"] = "unapproved-synthetic-group"
    path.write_text(json.dumps(summary), encoding="utf-8")
    os.utime(foreign / "READY", (started.timestamp(), started.timestamp()))
    unpublished = inbox / "incoming" / str(uuid4())
    unpublished.mkdir()
    os.utime(unpublished, (started.timestamp(), started.timestamp()))
    result = _call(capsys, "inbox", "prune-expired")["result"]
    assert result["expired_bundles"] == 0
    assert foreign.is_dir() and unpublished.is_dir()
