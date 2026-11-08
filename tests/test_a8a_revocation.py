"""Synthetic grant revocation acceptance on PostgreSQL 17."""

import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a4c_seller import envelope

from paiya.grants.service import revoke_grant
from paiya.ingest.service import ingest_fixture
from paiya.scheduler.queue import claim_enrich_next, claim_next
from paiya.store.fixture_scope import FixtureGrantUnavailable, seed_fixture_scope
from paiya.store.tables import (
    evidence,
    jobs,
    observations,
    price_facts,
    source_grants,
    status_facts,
    tags,
)


def add_job(engine, scope, kind, state="queued", grant_id=None):
    now = datetime.now(UTC)
    identifier = uuid4()
    with engine.begin() as conn:
        conn.execute(
            insert(jobs).values(
                id=identifier,
                group_id=scope.group_id,
                grant_id=grant_id or scope.grant_id,
                kind=kind,
                target_key=f"synthetic-target-{identifier}",
                logical_key=f"synthetic:{identifier}",
                due_at=now - timedelta(minutes=10),
                state=state,
                lease_token=uuid4() if state == "leased" else None,
                lease_until=now + timedelta(minutes=5) if state == "leased" else None,
            )
        )
    return identifier


def cli(*args):
    result = subprocess.run(
        [sys.executable, "-m", "paiya.cli", "grant", "revoke", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=10,
        check=False,
    )
    assert len(result.stdout.splitlines()) == 1
    return result.returncode, json.loads(result.stdout)


def test_cli_unknown_and_invalid_grants_are_not_found(pg_engine):
    for grant_id in ("not-a-uuid", str(uuid4())):
        code, result = cli(grant_id)
        assert code != 0
        assert result["error"]["code"] == "scope_not_found"


def test_cli_revoke_cancels_only_active_jobs_and_is_idempotent(pg_engine):
    scope = seed_fixture_scope(pg_engine, "synthetic-a8-cancel")
    other = uuid4()
    now = datetime.now(UTC)
    with pg_engine.begin() as conn:
        conn.execute(
            insert(source_grants).values(
                id=other,
                group_id=scope.group_id,
                source_kind="fixture",
                scopes=["capture", "read"],
                authority_ref="synthetic-other",
                valid_from=now,
                retention_days=30,
            )
        )
    active = [
        add_job(pg_engine, scope, kind, state)
        for kind in ("discover", "revisit", "enrich", "manual_import", "purge")
        for state in ("queued", "leased")
    ]
    retained = [
        add_job(pg_engine, scope, "manual_import", state) for state in ("succeeded", "failed")
    ]
    other_job = add_job(pg_engine, scope, "manual_import", grant_id=other)
    code, response = cli(str(scope.grant_id))
    result = response["result"]
    assert code == 0 and response["ok"]
    assert result["grant_id"] == str(scope.grant_id)
    assert result["revoked_at"].endswith("Z")
    assert result["already_revoked"] is False and result["cancelled_jobs"] == 10
    assert "synthetic-target" not in json.dumps(response)
    with pg_engine.connect() as conn:
        rows = {r.id: r for r in conn.execute(select(jobs)).all()}
    for identifier in active:
        assert (
            rows[identifier].state,
            rows[identifier].lease_token,
            rows[identifier].lease_until,
        ) == ("cancelled", None, None)
    assert [rows[i].state for i in retained] == ["succeeded", "failed"]
    assert rows[other_job].state == "queued"
    later = add_job(pg_engine, scope, "purge")
    code, repeat = cli(str(scope.grant_id))
    assert code == 0
    assert repeat["result"] == {**result, "already_revoked": True, "cancelled_jobs": 1}
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(jobs.c.state).where(jobs.c.id == later)).scalar_one() == "cancelled"
        )


def test_non_synthetic_scope_is_denied_without_changes(pg_engine):
    scope = seed_fixture_scope(pg_engine, "synthetic-boundary")
    job_id = add_job(pg_engine, scope, "discover")
    from paiya.store.tables import groups

    with pg_engine.begin() as conn:
        conn.execute(
            update(groups)
            .where(groups.c.id == scope.group_id)
            .values(group_key="test-only-non-synthetic")
        )
    code, result = cli(str(scope.grant_id))
    assert code != 0 and result["error"]["code"] == "scope_not_permitted"
    with pg_engine.connect() as conn:
        assert conn.execute(select(source_grants.c.revoked_at)).scalar_one() is None
        assert (
            conn.execute(select(jobs.c.state).where(jobs.c.id == job_id)).scalar_one() == "queued"
        )


@pytest.mark.parametrize("kind,claim", [("discover", claim_next), ("enrich", claim_enrich_next)])
def test_locked_grant_defers_claim_and_job_is_cancelled(pg_engine, kind, claim):
    scope = seed_fixture_scope(pg_engine, f"synthetic-a8-lock-{kind}")
    job_id = add_job(pg_engine, scope, kind)
    with pg_engine.connect() as locker:
        transaction = locker.begin()
        locker.execute(
            select(source_grants).where(source_grants.c.id == scope.grant_id).with_for_update()
        )
        started = time.monotonic()
        with pg_engine.connect() as conn:
            conn.exec_driver_sql("SET statement_timeout = '1500ms'")
            conn.commit()
            assert claim(conn, datetime.now(UTC)) is None
        assert time.monotonic() - started < 2
        with pg_engine.connect() as conn:
            assert (
                conn.execute(select(jobs.c.state).where(jobs.c.id == job_id)).scalar_one()
                == "queued"
            )
        locker.execute(
            update(source_grants)
            .where(source_grants.c.id == scope.grant_id)
            .values(revoked_at=datetime.now(UTC))
        )
        locker.execute(update(jobs).where(jobs.c.id == job_id).values(state="cancelled"))
        transaction.commit()
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(jobs.c.state).where(jobs.c.id == job_id)).scalar_one()
            == "cancelled"
        )


def test_ingest_after_revoke_writes_nothing(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-a8-ingest")
    doc = envelope(scope, "synthetic-post")
    revoke_grant(pg_engine, scope.grant_id)
    tables = (observations, evidence, price_facts, status_facts, tags, jobs)

    def counts():
        with pg_engine.connect() as conn:
            return [
                conn.execute(select(func.count()).select_from(table)).scalar_one()
                for table in tables
            ]

    before = counts()
    with pytest.raises(FixtureGrantUnavailable) as error:
        ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    assert error.value.code == "grant_unavailable"
    assert counts() == before


def test_revoke_between_ingest_checks_rolls_back_commit(pg_engine, tmp_path, monkeypatch):
    from paiya.ingest import service

    scope = seed_fixture_scope(pg_engine, "synthetic-a8-mid-ingest")
    original = service.verify_fixture_scope
    tables = (observations, evidence, price_facts, status_facts, tags, jobs)

    def counts():
        with pg_engine.connect() as conn:
            return [conn.execute(select(func.count()).select_from(t)).scalar_one() for t in tables]

    def revoke_after_initial_check(conn, checked_scope, purpose):
        original(conn, checked_scope, purpose)
        revoke_grant(pg_engine, scope.grant_id)

    monkeypatch.setattr(service, "verify_fixture_scope", revoke_after_initial_check)
    before = counts()
    with pytest.raises(FixtureGrantUnavailable) as error:
        ingest_fixture(pg_engine, scope, envelope(scope, "synthetic-mid"), {}, tmp_path)
    assert error.value.code == "grant_unavailable"
    assert counts() == before


def test_revoke_waits_for_ingest_commit(pg_engine, tmp_path, monkeypatch):
    from paiya.ingest import service

    scope = seed_fixture_scope(pg_engine, "synthetic-a8-ingest-lock")
    entered, release = Event(), Event()
    original = service.verify_fixture_scope_for_commit

    def hold_commit(*args):
        original(*args)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(service, "verify_fixture_scope_for_commit", hold_commit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ingest = pool.submit(
            ingest_fixture, pg_engine, scope, envelope(scope, "synthetic-lock"), {}, tmp_path
        )
        try:
            assert entered.wait(5)
            revoke = pool.submit(revoke_grant, pg_engine, scope.grant_id)
            time.sleep(0.25)
            assert not revoke.done()
        finally:
            release.set()
        oid = ingest.result(timeout=5)
        assert revoke.result(timeout=5)["already_revoked"] is False
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(observations.c.id).where(observations.c.id == oid)).scalar_one()
            == oid
        )
        assert conn.execute(select(source_grants.c.revoked_at)).scalar_one() is not None


def test_revoke_locks_grant_before_changing_it(pg_engine):
    from sqlalchemy import event

    scope = seed_fixture_scope(pg_engine, "synthetic-a8-lock-before-update")
    entered, release = Event(), Event()

    def pause_after_read(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "source_grants.revoked_at" in statement
        ):
            entered.set()
            assert release.wait(5)

    event.listen(pg_engine, "after_cursor_execute", pause_after_read)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(revoke_grant, pg_engine, scope.grant_id)
            try:
                assert entered.wait(5)
                with pg_engine.connect() as conn:
                    result = conn.execute(
                        select(source_grants.c.id)
                        .where(source_grants.c.id == scope.grant_id)
                        .with_for_update(read=True, skip_locked=True)
                    ).scalar_one_or_none()
                    assert result is None
            finally:
                release.set()
            future.result(timeout=5)
    finally:
        event.remove(pg_engine, "after_cursor_execute", pause_after_read)


def test_revoked_read_endpoints_and_all_mcp_tools(pg_engine, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_a2_ingest import attach_webp
    from test_a4c_seller import record
    from test_a6c_mcp import assert_error, session

    from paiya.api import evidence as api
    from paiya.api.app import app
    from paiya.mcp import server

    scope = seed_fixture_scope(pg_engine, "synthetic-a8-read")
    doc = envelope(scope, "synthetic-read")
    assets = attach_webp(doc)
    doc["assets"]["status"] = doc["assets"].pop("s01-open")
    ingest_fixture(pg_engine, scope, doc, assets, tmp_path)
    post, seller = record(pg_engine, scope, tmp_path, envelope(scope, "synthetic-second"))
    with pg_engine.connect() as conn:
        eid = conn.execute(
            select(evidence.c.id).where(evidence.c.media_state == "captured")
        ).scalar_one()
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", scope.group_key)
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(api, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(server, "get_database_engine", lambda: pg_engine)
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        assert client.get(f"/v1/evidence/{eid}/media").status_code == 200
        revoke_grant(pg_engine, scope.grant_id)
        for path in (
            "/v1/posts",
            f"/v1/posts/{post}",
            f"/v1/evidence/{eid}",
            f"/v1/evidence/{eid}/media",
            "/v1/models",
            "/v1/prices/trends?model_key=synthetic",
        ):
            response = client.get(path)
            assert response.status_code == 403, (path, response.text)
            assert response.json()["detail"]["code"] == "grant_unavailable"

    async def check():
        async with session(scope.group_key) as client:
            for name, args in (
                ("search_posts", {}),
                ("get_post", {"post_id": str(post)}),
                ("get_seller", {"seller_id": str(seller)}),
                ("get_evidence", {"evidence_id": str(eid)}),
                ("get_price_trend", {"model_key": "synthetic"}),
            ):
                assert_error(await client.call_tool(name, args), "grant_unavailable")

    asyncio.run(check())


@pytest.mark.parametrize("kind,claim", [("discover", claim_next), ("enrich", claim_enrich_next)])
def test_expired_revoked_lease_is_cancelled_on_reclaim(pg_engine, kind, claim):
    scope = seed_fixture_scope(pg_engine, f"synthetic-a8-expired-{kind}")
    revoke_grant(pg_engine, scope.grant_id)
    job_id = add_job(pg_engine, scope, kind, "leased")
    now = datetime.now(UTC)
    with pg_engine.begin() as conn:
        conn.execute(
            update(jobs).where(jobs.c.id == job_id).values(lease_until=now - timedelta(seconds=1))
        )
    with pg_engine.connect() as conn:
        assert claim(conn, now) is None
    with pg_engine.connect() as conn:
        row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().one()
        assert (row["state"], row["retry_count"], row["lease_token"], row["lease_until"]) == (
            "cancelled",
            0,
            None,
            None,
        )


def test_claim_skips_locked_job(pg_engine):
    scope = seed_fixture_scope(pg_engine, "synthetic-a8-job-lock")
    job_id = add_job(pg_engine, scope, "discover")
    with pg_engine.connect() as locker:
        transaction = locker.begin()
        locker.execute(select(jobs).where(jobs.c.id == job_id).with_for_update())
        with pg_engine.connect() as conn:
            conn.exec_driver_sql("SET statement_timeout = '1500ms'")
            conn.commit()
            started = time.monotonic()
            assert claim_next(conn, datetime.now(UTC)) is None
            assert time.monotonic() - started < 2
        transaction.rollback()


def test_enrich_defers_store_while_revocation_locked(pg_engine, tmp_path, monkeypatch):
    import httpx
    from test_a6b_enrichment import model_tags, prepare, proposal, response

    from paiya.enrich.service import run_enrich_once

    scope, _, _, now = prepare(pg_engine, tmp_path, monkeypatch, model="RTX 3060")
    locker = pg_engine.connect()
    transaction = locker.begin()
    before = model_tags(pg_engine)

    def during_model_call(request):
        locker.execute(
            select(source_grants).where(source_grants.c.id == scope.grant_id).with_for_update()
        )
        locker.execute(
            update(source_grants).where(source_grants.c.id == scope.grant_id).values(revoked_at=now)
        )
        return response([proposal()])

    try:
        result = run_enrich_once(
            pg_engine, transport=httpx.MockTransport(during_model_call), now=now
        )
        assert result["state"] == "leased" and result["status"] == "grant_check_deferred"
        assert model_tags(pg_engine) == before
        transaction.commit()
    finally:
        if transaction.is_active:
            transaction.rollback()
        locker.close()
    with pg_engine.connect() as conn:
        assert claim_enrich_next(conn, now + timedelta(minutes=10)) is None
    with pg_engine.connect() as conn:
        row = conn.execute(select(jobs).where(jobs.c.kind == "enrich")).mappings().one()
        assert row["state"] == "cancelled" and row["retry_count"] == 0


def test_enrich_run_once_does_not_process_revoked_grant(pg_engine, tmp_path, monkeypatch):
    import httpx
    from test_a6b_enrichment import prepare

    from paiya.enrich.service import run_enrich_once

    scope, _, _, now = prepare(pg_engine, tmp_path, monkeypatch)
    revoke_grant(pg_engine, scope.grant_id)
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request))
    assert run_enrich_once(pg_engine, transport=transport, now=now) is None
    assert calls == []


def test_revoke_and_claims_do_not_deadlock(pg_engine):
    import random

    rng = random.Random(64)
    for iteration in range(20):
        scope = seed_fixture_scope(pg_engine, f"synthetic-a8-race-{iteration}")
        job_ids = [add_job(pg_engine, scope, kind) for kind in ("discover", "enrich")]

        def run_claim(claim, delay):
            time.sleep(delay)
            with pg_engine.connect() as conn:
                conn.exec_driver_sql("SET statement_timeout = '2s'")
                conn.commit()
                return claim(conn, datetime.now(UTC))

        def run_revoke(delay):
            time.sleep(delay)
            return revoke_grant(pg_engine, scope.grant_id)

        delays = [rng.uniform(0, 0.01) for _ in range(3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(run_revoke, delays[0]),
                pool.submit(run_claim, claim_next, delays[1]),
                pool.submit(run_claim, claim_enrich_next, delays[2]),
            ]
            for future in futures:
                future.result(timeout=5)
        with pg_engine.connect() as conn:
            states = (
                conn.execute(select(jobs.c.state).where(jobs.c.id.in_(job_ids))).scalars().all()
            )
            assert states == ["cancelled", "cancelled"]
