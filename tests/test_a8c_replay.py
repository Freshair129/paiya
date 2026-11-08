"""Synthetic purge replay and fail-closed startup contract tests (FR-13, AT-09)."""

import asyncio
import hashlib
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, select, update
from test_a2_ingest import WEBP
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a6c_mcp import session
from test_a8a_revocation import add_job
from test_a8b_purge import image_document, second_grant, snapshot
from test_a8b_purge import paths as paths  # noqa: F401

from paiya import cli, serve
from paiya.api import evidence as api
from paiya.api.app import app
from paiya.api.evidence import get_db_connection, require_fixture_scope
from paiya.grants.service import revoke_grant
from paiya.ingest.service import ingest_fixture
from paiya.mcp import server
from paiya.purge import service
from paiya.purge.errors import PurgeError
from paiya.purge.service import replay_purge_log, run_purge
from paiya.store.fixture_scope import seed_fixture_scope
from paiya.store.tables import (
    evidence,
    groups,
    jobs,
    metadata,
    observations,
    posts,
    purge_jobs,
    source_grants,
)


def entry():
    return {
        "id": str(uuid4()),
        "scope_kind": "grant",
        "scope_key": str(uuid4()),
        "requested_at": "2026-11-08T00:00:00Z",
    }


def write_log(path, records):
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def write_invalid_log(log, problem):
    record = entry()
    invalid = dict(record)
    if problem == "missing":
        del invalid["scope_key"]
    elif problem == "extra":
        invalid["extra"] = "synthetic-private"
    elif problem == "uuid":
        invalid["id"] = "invalid"
    elif problem == "kind":
        invalid["scope_kind"] = "unknown"
    elif problem == "time":
        invalid["requested_at"] = "2026-11-08T07:00:00+07:00"
    elif problem == "duplicate":
        invalid["scope_key"] = str(uuid4())
    if problem == "json":
        log.write_text("secret-synthetic-invalid\n", encoding="utf-8")
    elif problem == "blank":
        log.write_text("\n", encoding="utf-8")
    else:
        write_log(log, [record, invalid] if problem == "duplicate" else [invalid])


@pytest.fixture
def purge_writes(pg_engine):
    statements = []

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        if re.match(r"\s*(INSERT|UPDATE|DELETE|MERGE|TRUNCATE)\b", statement, re.I) and re.search(
            r"\bpurge_jobs\b", statement, re.I
        ):
            statements.append(statement)

    event.listen(pg_engine, "before_cursor_execute", observe)
    try:
        yield statements
    finally:
        event.remove(pg_engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("target", ["serve", "mcp", "run", "run-once"])
@pytest.mark.parametrize("state", ["unset", "missing", "succeeded"])
def test_startup_allows_clear_log(
    pg_engine, paths, monkeypatch, capsys, target, state, purge_writes
):
    from paiya.enrich import service as enrich

    _, log = paths
    if state == "unset":
        monkeypatch.delenv("PAIYA_PURGE_LOG_PATH")
    elif state == "succeeded":
        record = entry()
        write_log(log, [record])
        with pg_engine.begin() as conn:
            conn.execute(
                insert(purge_jobs).values(
                    id=record["id"],
                    scope_kind="grant",
                    scope_key=record["scope_key"],
                    requested_at=datetime(2026, 11, 8, tzinfo=UTC),
                    state="succeeded",
                    result_counts={},
                )
            )
    with pg_engine.connect() as conn:
        before = conn.execute(select(purge_jobs)).all()
    other_rows = snapshot(pg_engine)
    purge_writes.clear()
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-a8c")
    monkeypatch.setattr("paiya.settings.get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(cli, "get_database_engine", lambda: pg_engine)
    reached = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: reached.append("serve"))
    monkeypatch.setattr(server, "create_server", lambda *args: None)

    async def stop_stdio(_server):
        reached.append("mcp")

    monkeypatch.setattr(server, "_run_stdio", stop_stdio)
    monkeypatch.setattr(enrich, "run_enrich_once", lambda *args, **kwargs: None)
    monkeypatch.setattr(enrich, "run_enrich_worker", lambda *args: None)
    if target == "serve":
        serve.main()
        assert reached == ["serve"]
    elif target == "mcp":
        server.main()
        assert reached == ["mcp"]
    else:
        assert cli.main(["enrich", target]) == 0
    assert "purge_" not in capsys.readouterr().err
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).all() == before
    assert snapshot(pg_engine) == other_rows
    assert purge_writes == []


@pytest.mark.parametrize("target", ["serve", "mcp", "run", "run-once"])
@pytest.mark.parametrize("state", ["failed", "running", "missing"])
def test_pending_ids_refuse_startup_without_writes(
    pg_engine, paths, monkeypatch, capsys, target, state, purge_writes
):
    from paiya.enrich import service as enrich

    _, log = paths
    record = entry()
    write_log(log, [record])
    if state != "missing":
        with pg_engine.begin() as conn:
            conn.execute(
                insert(purge_jobs).values(
                    id=record["id"],
                    scope_kind="grant",
                    scope_key=record["scope_key"],
                    requested_at=datetime(2026, 11, 8, tzinfo=UTC),
                    state=state,
                    result_counts={},
                )
            )
    with pg_engine.connect() as conn:
        before = conn.execute(select(purge_jobs)).all()
    other_rows = snapshot(pg_engine)
    purge_writes.clear()
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-a8c")
    monkeypatch.setattr("paiya.settings.get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(cli, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(enrich, "run_enrich_once", lambda *args, **kwargs: None)
    monkeypatch.setattr(enrich, "run_enrich_worker", lambda *args: None)
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: pytest.fail("opened server"))
    monkeypatch.setattr(server, "create_server", lambda *args: pytest.fail("opened MCP"))
    if target in ("serve", "mcp"):
        with pytest.raises(SystemExit) as exc:
            (serve.main if target == "serve" else server.main)()
        assert exc.value.code == 2
        output = capsys.readouterr().err
    else:
        assert cli.main(["enrich", target]) == 1
        output = capsys.readouterr().out
    assert "purge_replay_required" in output
    assert "1 pending" in output
    assert record["id"] not in output and record["scope_key"] not in output
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).all() == before
    assert snapshot(pg_engine) == other_rows
    assert purge_writes == []


@pytest.mark.parametrize("target", ["serve", "mcp", "run", "run-once"])
@pytest.mark.parametrize(
    "problem", ["json", "missing", "extra", "uuid", "kind", "time", "blank", "duplicate"]
)
def test_invalid_log_refuses_without_echoing_content(
    pg_engine, paths, monkeypatch, capsys, target, problem, purge_writes
):
    from paiya.enrich import service as enrich

    _, log = paths
    write_invalid_log(log, problem)
    before = snapshot(pg_engine)
    purge_writes.clear()
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-a8c")
    monkeypatch.setattr("paiya.settings.get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(cli, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(enrich, "run_enrich_once", lambda *args, **kwargs: None)
    monkeypatch.setattr(enrich, "run_enrich_worker", lambda *args: None)
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: pytest.fail("opened server"))
    monkeypatch.setattr(server, "create_server", lambda *args: pytest.fail("opened MCP"))
    if target in ("serve", "mcp"):
        with pytest.raises(SystemExit) as exc:
            (serve.main if target == "serve" else server.main)()
        assert exc.value.code == 2
        output = capsys.readouterr().err
    else:
        assert cli.main(["enrich", target]) == 1
        output = capsys.readouterr().out
    assert "purge_log_invalid" in output
    assert "secret-synthetic-invalid" not in output
    assert "synthetic-private" not in output
    assert snapshot(pg_engine) == before
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).all() == []
    assert purge_writes == []


@pytest.mark.parametrize("state", ["failed", "running", "missing"])
def test_replay_reuses_identity_and_missing_scope_succeeds(pg_engine, paths, state):
    _, log = paths
    record = entry()
    write_log(log, [record])
    original = log.read_bytes()
    if state != "missing":
        with pg_engine.begin() as conn:
            conn.execute(
                insert(purge_jobs).values(
                    id=record["id"],
                    scope_kind="grant",
                    scope_key=record["scope_key"],
                    requested_at=datetime(2026, 11, 8, tzinfo=UTC),
                    state=state,
                    result_counts={},
                )
            )
    first = replay_purge_log(pg_engine)
    assert first["pending_count"] == 0
    assert first["results"][0]["purge_id"] == record["id"]
    assert first["results"][0]["state"] == "succeeded"
    assert all(value in (0, []) for value in first["results"][0]["result_counts"].values())
    with pg_engine.connect() as conn:
        rows = conn.execute(select(purge_jobs)).all()
    assert len(rows) == 1
    assert replay_purge_log(pg_engine) == first
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).all() == rows
    assert log.read_bytes() == original


@pytest.mark.parametrize(
    "problem", ["json", "missing", "extra", "uuid", "kind", "time", "blank", "duplicate"]
)
def test_malformed_log_is_rejected(pg_engine, paths, problem):
    from paiya.purge.startup import check_purge_log

    _, log = paths
    write_invalid_log(log, problem)
    with pytest.raises(PurgeError) as exc:
        check_purge_log(pg_engine)
    assert exc.value.code == "purge_log_invalid"
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).all() == []


def test_replay_revokes_grant_before_purge(pg_engine, paths, monkeypatch):
    _, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8c-revoke")
    active = [add_job(pg_engine, scope, "manual_import", state) for state in ("queued", "leased")]
    actual_revoke = service.revoke_grant
    cancelled = []

    def capture_cancellation(engine, grant_id):
        result = actual_revoke(engine, grant_id)
        with engine.connect() as conn:
            cancelled.extend(
                conn.execute(select(jobs.c.state).where(jobs.c.id.in_(active))).scalars()
            )
        return result

    monkeypatch.setattr(service, "revoke_grant", capture_cancellation)
    record = entry()
    record["scope_key"] = str(scope.grant_id)
    write_log(log, [record])
    result = replay_purge_log(pg_engine)
    assert result["pending_count"] == 0
    assert cancelled == ["cancelled", "cancelled"]
    with pg_engine.connect() as conn:
        assert conn.execute(select(source_grants.c.revoked_at)).scalar_one() is not None
        assert conn.execute(select(purge_jobs.c.state)).scalar_one() == "succeeded"


def test_replay_stops_at_malformed_line(pg_engine, paths):
    _, log = paths
    first, last = entry(), entry()
    original = (json.dumps(first) + "\n{bad\n" + json.dumps(last) + "\n").encode()
    log.write_bytes(original)
    with pytest.raises(PurgeError) as exc:
        replay_purge_log(pg_engine)
    assert exc.value.code == "purge_log_invalid"
    with pg_engine.connect() as conn:
        rows = conn.execute(select(purge_jobs.c.id, purge_jobs.c.state)).all()
    assert [(str(row.id), row.state) for row in rows] == [(first["id"], "succeeded")]
    assert log.read_bytes() == original


def test_replay_stops_before_nonsynthetic_scope(pg_engine, paths):
    _, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8c-boundary")
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(group_key="real"))
    first, bad, last = entry(), entry(), entry()
    bad["scope_key"] = str(scope.grant_id)
    write_log(log, [first, bad, last])
    original = log.read_bytes()
    with pytest.raises(PurgeError) as exc:
        replay_purge_log(pg_engine)
    assert exc.value.code == "scope_not_permitted"
    with pg_engine.connect() as conn:
        assert conn.execute(select(source_grants.c.revoked_at)).scalar_one() is None
        rows = conn.execute(select(purge_jobs.c.id, purge_jobs.c.state)).all()
    assert [(str(row.id), row.state) for row in rows] == [(first["id"], "succeeded")]
    assert log.read_bytes() == original


@pytest.mark.parametrize("invalid", ["unset", "inside"])
def test_replay_refuses_invalid_log_path(pg_engine, paths, monkeypatch, invalid):
    root, _ = paths
    if invalid == "unset":
        monkeypatch.delenv("PAIYA_PURGE_LOG_PATH")
    else:
        monkeypatch.setenv("PAIYA_PURGE_LOG_PATH", str(root / "log.jsonl"))
    with pytest.raises(PurgeError):
        replay_purge_log(pg_engine)
    with pg_engine.connect() as conn:
        assert conn.execute(select(purge_jobs)).all() == []


def test_replay_another_succeeded_scope_only_retries_files(pg_engine, paths):
    _, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8c-repeat")
    revoke_grant(pg_engine, scope.grant_id)
    first = run_purge(pg_engine, f"grant:{scope.grant_id}")
    record = entry()
    record["scope_key"] = str(scope.grant_id)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    before = log.read_bytes()
    result = replay_purge_log(pg_engine)
    assert result["pending_count"] == 0
    assert result["results"][0]["purge_id"] == first["purge_id"]
    assert result["results"][1]["purge_id"] == record["id"]
    assert all(value in (0, []) for value in result["results"][1]["result_counts"].values())
    assert log.read_bytes() == before


def test_replay_retries_pending_file_from_prior_success(pg_engine, paths, monkeypatch):
    root, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8c-pending")
    ingest_fixture(pg_engine, scope, image_document(scope, "private"), {"image": WEBP}, root)
    revoke_grant(pg_engine, scope.grant_id)
    real_unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path.suffix == ".webp":
            raise PermissionError("synthetic locked file")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked)
        first = run_purge(pg_engine, f"grant:{scope.grant_id}")
    assert first["result_counts"]["files_pending"] == 1
    pending_file = root / "assets" / first["result_counts"]["pending_storage_keys"][0]
    assert pending_file.exists()
    record = entry()
    record["scope_key"] = str(scope.grant_id)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    before = log.read_bytes()
    result = replay_purge_log(pg_engine)
    counts = result["results"][1]["result_counts"]
    assert counts["files_deleted"] == 1 and counts["files_pending"] == 0
    assert all(value in (0, []) for key, value in counts.items() if key != "files_deleted")
    assert not pending_file.exists()
    assert log.read_bytes() == before


def test_repair_commands_are_not_startup_gated(pg_engine, paths, monkeypatch, capsys):
    _, log = paths
    pending = entry()
    write_log(log, [pending])
    scope = seed_fixture_scope(pg_engine, "synthetic-a8c-repair")
    monkeypatch.setattr(cli, "get_database_engine", lambda: pg_engine)
    assert cli.main(["grant", "revoke", str(scope.grant_id)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert cli.main(["purge", "run", "--scope", f"grant:{scope.grant_id}"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "succeeded"
    assert cli.main(["purge", "replay"]) == 0
    assert json.loads(capsys.readouterr().out)["pending_count"] == 0


def test_concurrent_replays_serialize_same_scope(pg_engine, paths, monkeypatch):
    _, log = paths
    scope = seed_fixture_scope(pg_engine, "synthetic-a8c-concurrent")
    revoke_grant(pg_engine, scope.grant_id)
    first, second = entry(), entry()
    first["scope_key"] = second["scope_key"] = str(scope.grant_id)
    write_log(log, [first, second])
    entered, second_entered, release = Event(), Event(), Event()
    original = service._purge_rows
    calls = []

    def pause_first(*args):
        calls.append(1)
        if len(calls) > 1:
            second_entered.set()
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(service, "_purge_rows", pause_first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(replay_purge_log, pg_engine)
        try:
            assert entered.wait(10)
            two = pool.submit(replay_purge_log, pg_engine)
            assert not second_entered.wait(0.5)
        finally:
            release.set()
        assert one.result(timeout=20)["pending_count"] == 0
        assert two.result(timeout=20)["pending_count"] == 0
    assert len(calls) == 1
    with pg_engine.connect() as conn:
        rows = conn.execute(select(purge_jobs.c.id, purge_jobs.c.state)).all()
    assert {str(row.id) for row in rows} == {first["id"], second["id"]}
    assert all(row.state == "succeeded" for row in rows)


def test_restore_drill_replays_original_command(pg_engine, paths, tmp_path, monkeypatch, capsys):
    root, log = paths
    a = seed_fixture_scope(pg_engine, "synthetic-a8c-restore")
    b = second_grant(pg_engine, a)
    private = WEBP + b"synthetic-private"
    ingest_fixture(pg_engine, a, image_document(a, "shared"), {"image": WEBP}, root)
    b_doc = image_document(b, "shared")
    private_doc = image_document(a, "private", private)
    for doc, model, amount in ((b_doc, "RTX 3060", "5500"), (private_doc, "RTX 3070", "7500")):
        doc["payload"]["items"][0]["model"] = model
        doc["payload"]["items"][0]["price"] = {
            "kind": "ask",
            "unit": "each",
            "amount": amount,
            "currency": "THB",
        }
        doc["payload"]["evidence"][0]["source_span"]["quote"] = f"{model} {amount} เปิดขาย"
    kept = ingest_fixture(pg_engine, b, b_doc, {"image": WEBP}, root)
    removed = ingest_fixture(pg_engine, a, private_doc, {"image": private}, root)
    with pg_engine.connect() as conn:
        private_post = conn.execute(
            select(observations.c.post_id).where(observations.c.id == removed)
        ).scalar_one()
        private_evidence = conn.execute(
            select(evidence.c.id).where(evidence.c.observation_id == removed)
        ).first()[0]
        kept_post = conn.execute(
            select(observations.c.post_id).where(observations.c.id == kept)
        ).scalar_one()
        seller = conn.execute(select(posts.c.seller_id).where(posts.c.id == kept_post)).scalar_one()
    backup = tmp_path / "backup"
    backup.mkdir()
    shutil.copytree(root, backup / "media")
    with pg_engine.connect() as conn:
        names = set(
            conn.exec_driver_sql(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            ).scalars()
        )
        ordered = ["alembic_version"] + [table.name for table in metadata.sorted_tables]
        assert set(ordered) == names
        raw = conn.connection.driver_connection
        with raw.cursor() as cur:
            for name in ordered:
                with (backup / name).open("wb") as destination:
                    with cur.copy(f'COPY "{name}" TO STDOUT (FORMAT binary)') as copy:
                        for chunk in copy:
                            destination.write(chunk)
    revoke_grant(pg_engine, a.grant_id)
    purged = run_purge(pg_engine, f"grant:{a.grant_id}")
    original_log = log.read_bytes()
    with pg_engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE " + ", ".join(f'"{name}"' for name in ordered) + " CASCADE")
        raw = conn.connection.driver_connection
        with raw.cursor() as cur:
            for name in ordered:
                with (backup / name).open("rb") as source:
                    with cur.copy(f'COPY "{name}" FROM STDIN (FORMAT binary)') as copy:
                        copy.write(source.read())
    shutil.copytree(backup / "media", root, dirs_exist_ok=True)
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", a.group_key)
    monkeypatch.setattr("paiya.settings.get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(cli, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: pytest.fail("opened server"))
    real_create_server = server.create_server
    monkeypatch.setattr(server, "create_server", lambda *args: pytest.fail("opened MCP"))
    for main in (serve.main, server.main):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
        assert "purge_replay_required" in capsys.readouterr().err
    assert cli.main(["enrich", "run-once"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "purge_replay_required"
    assert cli.main(["purge", "replay"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["results"][0]["purge_id"] == purged["purge_id"]
    assert result["pending_count"] == 0
    assert log.read_bytes() == original_log
    with pg_engine.connect() as conn:
        remaining = conn.execute(select(observations.c.id)).scalars().all()
    assert remaining == [kept] and removed not in remaining
    assert (root / "assets" / f"{hashlib.sha256(WEBP).hexdigest()}.webp").exists()
    assert not (root / "assets" / f"{hashlib.sha256(private).hexdigest()}.webp").exists()
    from paiya.purge.startup import check_purge_log

    check_purge_log(pg_engine)
    monkeypatch.setattr(server, "create_server", real_create_server)
    monkeypatch.setattr(server, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(api, "get_database_engine", lambda: pg_engine)

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    app.dependency_overrides[require_fixture_scope] = lambda: b

    async def read_after_restore():
        async with session(b.group_key) as mcp:
            with TestClient(app) as client:
                assert client.get(f"/v1/posts/{private_post}").status_code == 404
                assert client.get(f"/v1/evidence/{private_evidence}").status_code == 404
                assert client.get(f"/v1/evidence/{private_evidence}/media").status_code == 404
                assert client.get(f"/v1/posts/{kept_post}").status_code == 200
                models = client.get("/v1/models").json()["models"]
                assert {model["model_key"] for model in models} == {"rtx-3060"}
                assert (
                    client.get("/v1/prices/trends?model_key=rtx-3070").json()["current"]["n"] == 0
                )
                for path in (
                    "/v1/posts",
                    "/v1/models",
                    "/v1/prices/trends?model_key=rtx-3060",
                    f"/v1/sellers/{seller}",
                ):
                    response = client.get(path)
                    assert response.status_code == 200
                    assert str(private_post) not in response.text
                for tool, args in (
                    ("get_post", {"post_id": str(private_post)}),
                    ("get_evidence", {"evidence_id": str(private_evidence)}),
                ):
                    result = await mcp.call_tool(tool, args)
                    assert result.is_error and result.structured_content == {"error": "not_found"}
                for tool, args in (
                    ("search_posts", {}),
                    ("get_seller", {"seller_id": str(seller)}),
                    ("get_price_trend", {"model_key": "rtx-3060"}),
                ):
                    result = await mcp.call_tool(tool, args)
                    assert not result.is_error
                    assert str(private_post) not in json.dumps(result.structured_content)

    try:
        asyncio.run(read_after_restore())
    finally:
        app.dependency_overrides.clear()
