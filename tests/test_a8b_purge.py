"""Synthetic-only purge contract tests on isolated PostgreSQL schemas."""

import copy
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, select, update
from sqlalchemy.exc import IntegrityError
from test_a2_ingest import WEBP
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a4c_seller import envelope
from test_a6a_extraction import capture

from paiya.api.app import app
from paiya.api.evidence import get_db_connection, require_fixture_scope
from paiya.grants.service import revoke_grant
from paiya.ingest.service import ingest_fixture
from paiya.purge.errors import PurgeError
from paiya.purge.service import run_purge
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.media import lock_media_asset_for_transaction
from paiya.store.tables import (
    capture_gaps,
    capture_runs,
    comment_versions,
    comments,
    current_items,
    evidence,
    groups,
    jobs,
    listing_items,
    media_assets,
    observations,
    posts,
    price_facts,
    purge_jobs,
    sale_events,
    sellers,
    source_grants,
    status_facts,
    tags,
    watches,
)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    log = tmp_path / "purge.jsonl"
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(root))
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(log))
    return root, log


def second_grant(engine, scope):
    other = FixtureScope(scope.group_id, uuid4(), scope.group_key)
    with engine.begin() as conn:
        conn.execute(
            insert(source_grants).values(
                id=other.grant_id,
                group_id=other.group_id,
                source_kind="fixture",
                scopes=["read", "capture", "media", "comments"],
                authority_ref="synthetic-second",
                valid_from=datetime.now(UTC),
                retention_days=30,
            )
        )
    return other


def snapshot(engine):
    with engine.connect() as conn:
        names = conn.exec_driver_sql(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() "
            "AND tablename NOT IN ('alembic_version', 'purge_jobs') ORDER BY tablename"
        ).scalars()
        return {
            name: sorted(
                conn.exec_driver_sql(f'SELECT row_to_json(t)::text FROM "{name}" t').scalars()
            )
            for name in names
        }


def test_grant_purge_keeps_other_grant_and_rebuilds_current(pg_engine, paths):
    root, _ = paths
    a = seed_fixture_scope(pg_engine, "synthetic-a8b-shared")
    b = second_grant(pg_engine, a)
    b_doc = image_document(b, "shared")
    b_doc["payload"]["items"][0]["model"] = "RTX 3060 12GB"
    b_doc["payload"]["items"][0]["price"] = {
        "kind": "ask",
        "unit": "each",
        "amount": "5500",
        "currency": "THB",
    }
    b_doc["payload"]["evidence"][0]["source_span"]["quote"] = "RTX 3060 12GB 5500 เปิดขาย"
    b_doc["payload"]["comments"] = [
        {
            "source_comment_key": "synthetic-b-comment",
            "role": "other",
            "actor_key": "synthetic-buyer",
            "text": "Synthetic buyer comment",
            "source_time": {
                "source_text": "2026-11-07T09:00:00Z",
                "value": "2026-11-07T09:00:00Z",
                "precision": "exact",
            },
        }
    ]
    b_doc["payload"]["activity"]["distinct_comments_seen"] = 1
    b_doc["payload"]["activity"]["distinct_known_commenters_seen"] = 1
    b_observation = ingest_fixture(pg_engine, b, b_doc, {"image": WEBP}, root)
    doc = image_document(a, "shared")
    doc["payload"]["observed_at"] = "2026-11-07T09:10:00Z"
    a_observation = ingest_fixture(pg_engine, a, doc, {"image": WEBP}, root)
    sample = (
        Path(__file__).resolve().parents[1]
        / "fixtures/scenarios/S03-multi-item/rounds/02-buyer-only.json"
    )
    a_only_doc = json.loads(sample.read_text(encoding="utf-8"))
    a_only_doc["payload"]["group_key"] = a.group_key
    a_only = ingest_fixture(pg_engine, a, a_only_doc, {}, root)
    with pg_engine.connect() as conn:
        before = conn.execute(select(observations).where(observations.c.id == b_observation)).one()
        b_rows = {
            table.name: conn.execute(
                select(table).where(table.c.observation_id == b_observation)
            ).all()
            for table in (evidence, price_facts, status_facts, tags, comment_versions)
        }
        b_jobs = conn.execute(select(jobs).where(jobs.c.grant_id == b.grant_id)).all()
        b_runs = conn.execute(
            select(capture_runs).where(capture_runs.c.grant_id == b.grant_id)
        ).all()
        assert all(
            b_rows[table.name] for table in (price_facts, status_facts, tags, comment_versions)
        )
        shared_post = before.post_id
        assert (
            conn.execute(
                select(current_items.c.last_observation_id).where(
                    current_items.c.post_id == shared_post
                )
            ).scalar_one()
            == a_observation
        )
        item_id = conn.execute(
            select(listing_items.c.id).where(listing_items.c.post_id == shared_post)
        ).scalar_one()
        a_only_post = conn.execute(
            select(observations.c.post_id).where(observations.c.id == a_only)
        ).scalar_one()
        a_only_item = conn.execute(
            select(listing_items.c.id).where(listing_items.c.post_id == a_only_post).limit(1)
        ).scalar_one()
        assert conn.execute(select(comments).where(comments.c.post_id == a_only_post)).first()
        job_a = conn.execute(
            select(jobs.c.id).where(jobs.c.grant_id == a.grant_id).limit(1)
        ).scalar_one()
        job_b = conn.execute(
            select(jobs.c.id).where(jobs.c.grant_id == b.grant_id).limit(1)
        ).scalar_one()
    now = datetime.now(UTC)
    gap_a, gap_b, watch = uuid4(), uuid4(), uuid4()
    with pg_engine.begin() as conn:
        for gap, job in [(gap_a, job_a), (gap_b, job_b)]:
            conn.execute(
                insert(capture_gaps).values(
                    id=gap,
                    group_id=a.group_id,
                    gap_from=now,
                    gap_to=now + timedelta(minutes=1),
                    reason="missed_slots",
                    detected_at=now,
                    compensated_by_job_id=job,
                )
            )
        conn.execute(
            insert(watches).values(id=watch, post_id=shared_post, item_id=item_id, saved_at=now)
        )
        conn.execute(
            insert(watches).values(
                id=uuid4(), post_id=a_only_post, item_id=a_only_item, saved_at=now
            )
        )
        b_gap = conn.execute(select(capture_gaps).where(capture_gaps.c.id == gap_b)).one()
    revoke_grant(pg_engine, a.grant_id)
    result = run_purge(pg_engine, f"grant:{a.grant_id}")
    assert result["state"] == "succeeded"
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(observations).where(observations.c.id == b_observation)).one()
            == before
        )
        for table in (evidence, price_facts, status_facts, tags, comment_versions):
            assert (
                conn.execute(select(table).where(table.c.observation_id == b_observation)).all()
                == b_rows[table.name]
            )
        assert conn.execute(select(jobs).where(jobs.c.grant_id == b.grant_id)).all() == b_jobs
        assert (
            conn.execute(select(capture_runs).where(capture_runs.c.grant_id == b.grant_id)).all()
            == b_runs
        )
        assert conn.execute(select(observations.c.id)).scalars().all() == [b_observation]
        assert conn.execute(select(posts.c.id)).scalars().all() == [shared_post]
        assert (
            conn.execute(select(current_items.c.last_observation_id)).scalar_one() == b_observation
        )
        assert (
            conn.execute(
                select(source_grants.c.revoked_at).where(source_grants.c.id == a.grant_id)
            ).scalar_one()
            is not None
        )
        assert conn.execute(select(watches.c.id)).scalars().all() == [watch]
        assert conn.execute(select(comments).where(comments.c.post_id == a_only_post)).all() == []
        assert (
            conn.execute(select(listing_items).where(listing_items.c.post_id == a_only_post)).all()
            == []
        )
        gaps = dict(
            conn.execute(select(capture_gaps.c.id, capture_gaps.c.compensated_by_job_id)).all()
        )
        assert gaps == {gap_a: None, gap_b: job_b}
        assert conn.execute(select(capture_gaps).where(capture_gaps.c.id == gap_b)).one() == b_gap
        assert len(conn.execute(select(sellers)).all()) == 1
        assert len(conn.execute(select(media_assets)).all()) == 1
        assert conn.execute(
            select(status_facts.c.observation_id).join(
                sale_events, sale_events.c.status_fact_id == status_facts.c.id
            )
        ).scalars().all() == [b_observation]
    assert (root / "assets" / f"{hashlib.sha256(WEBP).hexdigest()}.webp").exists()


def test_log_and_stdout_have_only_scope_metadata(pg_engine, paths):
    root, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-cli")
    sample = (
        Path(__file__).resolve().parents[1]
        / "fixtures/scenarios/S03-multi-item/rounds/02-buyer-only.json"
    )
    doc = json.loads(sample.read_text(encoding="utf-8"))
    doc["payload"]["group_key"] = scope.group_key
    doc["payload"]["seller"]["display_name"] = "SyntheticSellerSecret"
    doc["payload"]["items"][0]["model"] = "SyntheticTitleSecret"
    doc["payload"]["comments"][0]["text"] = "SyntheticCommentSecret"
    for entry in doc["payload"]["evidence"]:
        if entry["source_span"]["field"] == "comment_text":
            entry["source_span"]["quote"] = "SyntheticCommentSecret"
    ingest_fixture(pg_engine, scope, doc, {}, root)
    revoke_grant(pg_engine, scope.grant_id)
    proc = subprocess.run(
        [sys.executable, "-m", "paiya.cli", "purge", "run", "--scope", f"grant:{scope.grant_id}"],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(proc.stdout.splitlines()) == 1
    result = json.loads(proc.stdout)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry) == {"id", "scope_kind", "scope_key", "requested_at"}
    with pg_engine.connect() as conn:
        row = conn.execute(select(purge_jobs)).mappings().one()
    assert entry["id"] == result["purge_id"] == str(row["id"])
    assert result["scope"] == f"grant:{scope.grant_id}"
    assert result["state"] == "succeeded" and isinstance(result["result_counts"], dict)
    serialized = proc.stdout + lines[0] + str(row)
    for secret in ("SyntheticSellerSecret", "SyntheticTitleSecret", "SyntheticCommentSecret"):
        assert secret not in serialized


def test_log_is_written_before_database_insert(pg_engine, paths):
    _, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-order")
    revoke_grant(pg_engine, scope.grant_id)
    before = snapshot(pg_engine)

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("INSERT INTO purge_jobs"):
            raise RuntimeError("synthetic database failure")

    event.listen(pg_engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="synthetic database failure"):
            run_purge(pg_engine, f"grant:{scope.grant_id}")
    finally:
        event.remove(pg_engine, "before_cursor_execute", fail_insert)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    assert snapshot(pg_engine) == before
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).first() is None


def test_log_append_failure_changes_nothing(pg_engine, paths, monkeypatch):
    _, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-log-fail")
    revoke_grant(pg_engine, scope.grant_id)
    before = snapshot(pg_engine)
    original = Path.open

    def fail_open(path, *args, **kwargs):
        if path == log:
            raise OSError("synthetic log failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(PurgeError, match="log"):
        run_purge(pg_engine, f"grant:{scope.grant_id}")
    assert snapshot(pg_engine) == before
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).first() is None


def test_refusals_do_not_mutate_database(pg_engine, paths, monkeypatch):
    root, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-refusals")
    before = snapshot(pg_engine)
    for target, code in [
        (f"grant:{scope.grant_id}", "grant_not_revoked"),
        (f"grant:{uuid4()}", "scope_not_found"),
        (f"post:{uuid4()}", "scope_not_found"),
        (f"grant:{str(scope.grant_id).upper()}", "scope_format_invalid"),
        (str(scope.grant_id), "scope_format_invalid"),
    ]:
        with pytest.raises(PurgeError) as exc:
            run_purge(pg_engine, target)
        assert exc.value.code == code
        assert snapshot(pg_engine) == before
        with pg_engine.connect() as conn:
            assert conn.execute(select(purge_jobs)).first() is None
    revoke_grant(pg_engine, scope.grant_id)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).values(group_key="non-synthetic"))
    before = snapshot(pg_engine)
    with pytest.raises(PurgeError) as exc:
        run_purge(pg_engine, f"grant:{scope.grant_id}")
    assert exc.value.code == "scope_not_permitted"
    assert snapshot(pg_engine) == before
    with pg_engine.begin() as conn:
        conn.execute(update(groups).values(group_key=scope.group_key))
    before = snapshot(pg_engine)
    monkeypatch.delenv("PAIYA_PURGE_LOG_PATH")
    with pytest.raises(PurgeError) as exc:
        run_purge(pg_engine, f"grant:{scope.grant_id}")
    assert exc.value.code == "purge_log_path_required"
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(root / "inside.jsonl"))
    with pytest.raises(PurgeError) as exc:
        run_purge(pg_engine, f"grant:{scope.grant_id}")
    assert exc.value.code == "purge_log_path_not_permitted"
    monkeypatch.chdir(root.parent)
    monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(Path(root.name) / "inside-relative.jsonl"))
    with pytest.raises(PurgeError) as exc:
        run_purge(pg_engine, f"grant:{scope.grant_id}")
    assert exc.value.code == "purge_log_path_not_permitted"
    link = root.parent / "media-link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pass  # Windows may deny symlink creation without developer privileges.
    else:
        monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(link / "inside-symlink.jsonl"))
        with pytest.raises(PurgeError) as exc:
            run_purge(pg_engine, f"grant:{scope.grant_id}")
        assert exc.value.code == "purge_log_path_not_permitted"
    assert snapshot(pg_engine) == before
    assert not log.exists()
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).first() is None


def test_repeat_creates_empty_receipt_without_domain_changes(pg_engine, paths):
    root, _ = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-repeat")
    ingest_fixture(pg_engine, scope, envelope(scope, "post"), {}, root)
    revoke_grant(pg_engine, scope.grant_id)
    assert run_purge(pg_engine, f"grant:{scope.grant_id}")["state"] == "succeeded"
    before = snapshot(pg_engine)
    result = run_purge(pg_engine, f"grant:{scope.grant_id}")
    assert result["state"] == "succeeded"
    assert all(value == 0 or value == [] for value in result["result_counts"].values())
    assert snapshot(pg_engine) == before
    with pg_engine.connect() as conn:
        assert len(conn.execute(select(purge_jobs)).all()) == 2


def image_document(scope, post, data=WEBP):
    doc = envelope(scope, post)
    doc["assets"] = {
        "status": {
            "staging_key": "image",
            "sha256": hashlib.sha256(data).hexdigest(),
            "mime": "image/webp",
            "width": 2,
            "height": 2,
            "bytes": len(data),
        }
    }
    doc["payload"]["evidence"][0]["media_state"] = "captured"
    return doc


@pytest.mark.parametrize("reingest", [False, True])
def test_pending_file_retry_respects_new_evidence(pg_engine, paths, monkeypatch, reingest):
    root, _ = paths
    a = seed_fixture_scope(pg_engine, "synthetic-a8b-pending")
    b = second_grant(pg_engine, a)
    ingest_fixture(pg_engine, a, image_document(a, "old"), {"image": WEBP}, root)
    revoke_grant(pg_engine, a.grant_id)
    original = Path.unlink

    def locked(path, *args, **kwargs):
        if path.suffix == ".webp":
            raise PermissionError("synthetic lock")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked)
        first = run_purge(pg_engine, f"grant:{a.grant_id}")
    assert first["state"] == "succeeded"
    assert first["result_counts"]["files_pending"] == 1
    pending = first["result_counts"]["pending_storage_keys"]
    assert len(pending) == 1
    file = root / "assets" / pending[0]
    assert file.exists()
    if reingest:
        ingest_fixture(pg_engine, b, image_document(b, "new"), {"image": WEBP}, root)
    second = run_purge(pg_engine, f"grant:{a.grant_id}")
    assert second["result_counts"]["files_pending"] == 0
    assert second["result_counts"]["files_deleted"] == (0 if reingest else 1)
    assert file.exists() == reingest
    with pg_engine.connect() as conn:
        assert (conn.execute(select(media_assets)).first() is not None) == reingest


def test_grant_purge_deletes_only_unshared_media(pg_engine, paths):
    root, _ = paths
    a = seed_fixture_scope(pg_engine, "synthetic-a8b-media")
    b = second_grant(pg_engine, a)
    private = WEBP + b"synthetic-a-only"
    ingest_fixture(pg_engine, a, image_document(a, "shared"), {"image": WEBP}, root)
    ingest_fixture(pg_engine, b, image_document(b, "shared"), {"image": WEBP}, root)
    ingest_fixture(pg_engine, a, image_document(a, "a-only", private), {"image": private}, root)
    revoke_grant(pg_engine, a.grant_id)
    assert run_purge(pg_engine, f"grant:{a.grant_id}")["state"] == "succeeded"
    shared_digest = hashlib.sha256(WEBP).hexdigest()
    private_digest = hashlib.sha256(private).hexdigest()
    assert (root / "assets" / f"{shared_digest}.webp").exists()
    assert not (root / "assets" / f"{private_digest}.webp").exists()
    with pg_engine.connect() as conn:
        assert conn.execute(select(media_assets.c.storage_key)).scalars().all() == [shared_digest]


def test_transaction_failure_rolls_back_domain_and_files(pg_engine, paths, monkeypatch):
    import paiya.purge.service as service

    root, _ = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-rollback")
    ingest_fixture(pg_engine, scope, image_document(scope, "post"), {"image": WEBP}, root)
    revoke_grant(pg_engine, scope.grant_id)
    before = snapshot(pg_engine)
    files = list((root / "assets").iterdir())
    original = service._delete_count

    def fail_late(conn, table, predicate, key):
        if table.name == "observations":
            raise RuntimeError("synthetic failure after deleting facts")
        return original(conn, table, predicate, key)

    with monkeypatch.context() as patch:
        patch.setattr(service, "_delete_count", fail_late)
        result = run_purge(pg_engine, f"grant:{scope.grant_id}")
    assert result["state"] == "failed" and result["reason"] == "transaction_failed"
    assert snapshot(pg_engine) == before
    assert all(path.exists() for path in files)
    with pg_engine.connect() as conn:
        row = conn.execute(select(purge_jobs)).mappings().one()
        assert row["state"] == "failed" and row["reason"] == "transaction_failed"
    assert run_purge(pg_engine, f"grant:{scope.grant_id}")["state"] == "succeeded"


@pytest.mark.parametrize("state", ["queued", "leased"])
def test_post_purge_cancels_active_revisits_only(pg_engine, paths, state):
    root, _ = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-revisit")
    oid = ingest_fixture(pg_engine, scope, envelope(scope, "target"), {}, root)
    other_oid = ingest_fixture(pg_engine, scope, envelope(scope, "other"), {}, root)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
        other_post = conn.execute(
            select(observations.c.post_id).where(observations.c.id == other_oid)
        ).scalar_one()
    now = datetime.now(UTC)
    identifiers = [uuid4() for _ in range(3)]
    with pg_engine.begin() as conn:
        for identifier, target, job_state in zip(
            identifiers, [post_id, post_id, other_post], [state, "succeeded", "queued"], strict=True
        ):
            conn.execute(
                insert(jobs).values(
                    id=identifier,
                    group_id=scope.group_id,
                    grant_id=scope.grant_id,
                    kind="revisit",
                    target_key=str(target),
                    logical_key=f"synthetic:{identifier}",
                    due_at=now,
                    state=job_state,
                    lease_token=uuid4() if job_state == "leased" else None,
                    lease_until=now + timedelta(minutes=1) if job_state == "leased" else None,
                )
            )
    result = run_purge(pg_engine, f"post:{post_id}")
    assert result["state"] == "succeeded"
    with pg_engine.connect() as conn:
        rows = {r.id: r for r in conn.execute(select(jobs).where(jobs.c.id.in_(identifiers)))}
        assert (
            rows[identifiers[0]].state,
            rows[identifiers[0]].lease_token,
            rows[identifiers[0]].lease_until,
        ) == ("cancelled", None, None)
        assert rows[identifiers[1]].state == "succeeded"
        assert rows[identifiers[2]].state == "queued"
        assert conn.execute(select(observations.c.id)).scalars().all() == [other_oid]


def test_post_purge_waits_for_ingest_post_update(pg_engine, paths):
    root, _ = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-ingest-first")
    first = ingest_fixture(pg_engine, scope, envelope(scope, "shared"), {}, root)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == first)
        ).scalar_one()
    updated, release = Event(), Event()

    def pause_update(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE posts SET"):
            updated.set()
            if not release.wait(10):
                raise TimeoutError("ingest pause timed out")

    event.listen(pg_engine, "after_cursor_execute", pause_update)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ingest = pool.submit(
                ingest_fixture, pg_engine, scope, envelope(scope, "shared", minute=10), {}, root
            )
            assert updated.wait(5)
            purge = pool.submit(run_purge, pg_engine, f"post:{post_id}")
            try:
                with pytest.raises(TimeoutError):
                    purge.result(timeout=0.3)
            finally:
                release.set()
            new_id = ingest.result(timeout=10)
            assert purge.result(timeout=10)["state"] == "succeeded"
    finally:
        release.set()
        event.remove(pg_engine, "after_cursor_execute", pause_update)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(
                select(observations.c.id).where(observations.c.id.in_([first, new_id]))
            ).all()
            == []
        )


def test_ingest_after_purge_post_lock_has_no_partial_rows(pg_engine, paths, monkeypatch):
    import paiya.purge.service as service

    root, _ = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-purge-first")
    first = ingest_fixture(pg_engine, scope, envelope(scope, "shared"), {}, root)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == first)
        ).scalar_one()
    locked, release = Event(), Event()
    original = service._lock_scoped_posts

    def pause_lock(conn, kind, identifier):
        result = original(conn, kind, identifier)
        locked.set()
        if not release.wait(10):
            raise TimeoutError("purge pause timed out")
        return result

    monkeypatch.setattr(service, "_lock_scoped_posts", pause_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        purge = pool.submit(run_purge, pg_engine, f"post:{post_id}")
        assert locked.wait(5)
        ingest = pool.submit(
            ingest_fixture, pg_engine, scope, envelope(scope, "shared", minute=10), {}, root
        )
        try:
            with pytest.raises(TimeoutError):
                ingest.result(timeout=0.3)
        finally:
            release.set()
        assert purge.result(timeout=10)["state"] == "succeeded"
        try:
            new_id = ingest.result(timeout=10)
        except IntegrityError as error:
            assert error.orig.sqlstate == "23503"
            new_id = None
    with pg_engine.connect() as conn:
        remaining = conn.execute(select(observations.c.id)).scalars().all()
        assert remaining == ([] if new_id is None else [new_id])
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM observations o LEFT JOIN posts p "
                "ON p.id=o.post_id WHERE p.id IS NULL"
            ).scalar_one()
            == 0
        )
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM listing_items i LEFT JOIN posts p "
                "ON p.id=i.post_id WHERE p.id IS NULL"
            ).scalar_one()
            == 0
        )


def test_ingest_waits_for_media_asset_advisory_lock(pg_engine, paths):
    root, _ = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-media-lock")
    digest = hashlib.sha256(WEBP).hexdigest()
    with pg_engine.connect() as locker:
        transaction = locker.begin()
        lock_media_asset_for_transaction(locker, digest)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                ingest_fixture,
                pg_engine,
                scope,
                image_document(scope, "post"),
                {"image": WEBP},
                root,
            )
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.5)
            finally:
                transaction.commit()
            oid = future.result(timeout=10)
    with pg_engine.connect() as conn:
        assert conn.execute(
            select(evidence.c.id).where(
                evidence.c.observation_id == oid, evidence.c.media_asset_id.is_not(None)
            )
        ).one()
        assert conn.execute(select(media_assets.c.storage_key)).scalar_one() == digest
    assert (root / "assets" / f"{digest}.webp").exists()


def test_reads_after_purge_match_b_only_baseline(pg_engine, paths):
    root, _ = paths
    a = seed_fixture_scope(pg_engine, "synthetic-a8b-reads")
    b = second_grant(pg_engine, a)
    doc = capture(b, "RTX 3060", "", "RTX 3060", post="shared")
    observed = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    doc["payload"]["observed_at"] = observed
    doc["payload"]["posted_at"] = {
        "source_text": observed,
        "value": observed,
        "precision": "exact",
    }
    doc["payload"]["items"][0]["price"] = {
        "kind": "ask",
        "unit": "each",
        "amount": "5500",
        "currency": "THB",
    }
    doc["payload"]["evidence"][0]["source_span"]["quote"] = "RTX 3060 5500"
    oid = ingest_fixture(pg_engine, b, doc, {}, root)
    with pg_engine.connect() as conn:
        seller_id = conn.execute(
            select(posts.c.seller_id)
            .join(observations, observations.c.post_id == posts.c.id)
            .where(observations.c.id == oid)
        ).scalar_one()

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    app.dependency_overrides[require_fixture_scope] = lambda: b
    try:
        with TestClient(app) as client:
            paths_to_read = [
                "/v1/models",
                "/v1/prices/trends?model_key=rtx-3060",
                f"/v1/sellers/{seller_id}",
            ]

            def read_all():
                responses = [client.get(path) for path in paths_to_read]
                assert all(response.status_code == 200 for response in responses)
                return [response.json() for response in responses]

            baseline = read_all()
            assert baseline[0]["models"] == [{"model_key": "rtx-3060", "item_count": 1}]
            assert baseline[1]["current"]["n"] == 1
            doc_a = capture(
                a, "RTX 3070", "used", "RTX 3070", post="shared", observed="2026-11-16T12:00:00Z"
            )
            ingest_fixture(pg_engine, a, doc_a, {}, root)
            ingest_fixture(
                pg_engine, a, capture(a, "RTX 3060", "used", "RTX 3060", post="a-only"), {}, root
            )
            revoke_grant(pg_engine, a.grant_id)
            assert run_purge(pg_engine, f"grant:{a.grant_id}")["state"] == "succeeded"
            assert read_all() == baseline
    finally:
        app.dependency_overrides.clear()


def test_post_repeat_is_only_a_retry_after_reingest(pg_engine, paths):
    root, _ = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8b-post-repeat")
    oid = ingest_fixture(pg_engine, scope, envelope(scope, "shared"), {}, root)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
    assert run_purge(pg_engine, f"post:{post_id}")["state"] == "succeeded"
    new_id = ingest_fixture(pg_engine, scope, envelope(scope, "shared", minute=10), {}, root)
    before = snapshot(pg_engine)
    repeated = run_purge(pg_engine, f"post:{post_id}")
    assert repeated["state"] == "succeeded"
    assert all(value == 0 or value == [] for value in repeated["result_counts"].values())
    assert snapshot(pg_engine) == before
    with pg_engine.connect() as conn:
        assert conn.execute(select(observations.c.id)).scalars().all() == [new_id]


def test_ingest_same_media_waits_for_purge_file_delete(pg_engine, paths, monkeypatch):
    root, _ = paths
    a = seed_fixture_scope(pg_engine, "synthetic-a8b-media-race")
    b = second_grant(pg_engine, a)
    oid = ingest_fixture(pg_engine, a, image_document(a, "old"), {"image": WEBP}, root)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
    deleting, release = Event(), Event()
    original = Path.unlink

    def pause_unlink(path, *args, **kwargs):
        if path.suffix == ".webp":
            deleting.set()
            if not release.wait(10):
                raise TimeoutError("file delete pause timed out")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", pause_unlink)
    with ThreadPoolExecutor(max_workers=2) as pool:
        purge = pool.submit(run_purge, pg_engine, f"post:{post_id}")
        assert deleting.wait(5)
        ingest = pool.submit(
            ingest_fixture, pg_engine, b, image_document(b, "new"), {"image": WEBP}, root
        )
        try:
            with pytest.raises(TimeoutError):
                ingest.result(timeout=0.5)
        finally:
            release.set()
        assert purge.result(timeout=10)["state"] == "succeeded"
        new_id = ingest.result(timeout=10)
    with pg_engine.connect() as conn:
        assert conn.execute(
            select(evidence.c.id).where(
                evidence.c.observation_id == new_id, evidence.c.media_asset_id.is_not(None)
            )
        ).one()
        assert len(conn.execute(select(media_assets)).all()) == 1
    assert (root / "assets" / f"{hashlib.sha256(WEBP).hexdigest()}.webp").exists()


def test_post_purge_deletes_comments_replies_items_and_watches_of_all_grants(pg_engine, paths):
    root, _ = paths
    a = seed_fixture_scope(pg_engine, "synthetic-a8b-post-children")
    b = second_grant(pg_engine, a)
    sample = (
        Path(__file__).resolve().parents[1]
        / "fixtures/scenarios/S03-multi-item/rounds/02-buyer-only.json"
    )
    doc = json.loads(sample.read_text(encoding="utf-8"))
    doc["payload"]["group_key"] = a.group_key
    parent = doc["payload"]["comments"][0]
    reply = copy.deepcopy(parent)
    reply["source_comment_key"] = None
    reply["parent_key"] = parent["source_comment_key"]
    doc["payload"]["comments"].append(reply)
    doc["payload"]["activity"]["distinct_comments_seen"] = 2
    first = ingest_fixture(pg_engine, a, doc, {}, root)
    doc["run_id"] = str(uuid4())
    doc["capture_id"] = str(uuid4())
    second = ingest_fixture(pg_engine, b, doc, {}, root)
    other = ingest_fixture(pg_engine, b, envelope(b, "unrelated"), {}, root)
    with pg_engine.begin() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == first)
        ).scalar_one()
        other_row = conn.execute(select(observations).where(observations.c.id == other)).one()
        other_post = other_row.post_id
        post_tables = (
            observations,
            listing_items,
            current_items,
            sale_events,
            watches,
            comments,
            evidence,
            price_facts,
            status_facts,
            tags,
            comment_versions,
        )
        other_rows = {
            table.name: conn.execute(select(table).where(table.c.post_id == other_post)).all()
            for table in post_tables
            if "post_id" in table.c
        }
        item = conn.execute(
            select(listing_items.c.id).where(listing_items.c.post_id == post_id).limit(1)
        ).scalar_one()
        conn.execute(
            insert(watches).values(
                id=uuid4(), post_id=post_id, item_id=item, saved_at=datetime.now(UTC)
            )
        )
        assert len(conn.execute(select(comments).where(comments.c.post_id == post_id)).all()) >= 2
    assert run_purge(pg_engine, f"post:{post_id}")["state"] == "succeeded"
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(observations).where(observations.c.id == other)).one() == other_row
        )
        assert (
            conn.execute(
                select(observations.c.id).where(observations.c.id.in_([first, second]))
            ).all()
            == []
        )
        for table in (posts, comments, listing_items, watches):
            column = table.c.id if table is posts else table.c.post_id
            assert conn.execute(select(table).where(column == post_id)).all() == []
        for table in post_tables:
            if "post_id" in table.c:
                assert (
                    conn.execute(select(table).where(table.c.post_id == other_post)).all()
                    == other_rows[table.name]
                )
                assert conn.execute(select(table).where(table.c.post_id == post_id)).all() == []
