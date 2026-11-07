from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401

from paiya.groups.errors import GroupError
from paiya.groups.service import add_group, pause, resume, set_interval
from paiya.scheduler.errors import QueueError
from paiya.scheduler.queue import claim_next, complete, renew_lease, schedule_due
from paiya.store.tables import capture_gaps, capture_runs, groups, jobs, source_grants


def test_group_limit_concurrent_and_persistence(pg_engine):
    now = datetime.now(UTC)

    def add(i):
        try:
            with pg_engine.connect() as conn:
                return add_group(conn, now, f"synthetic-group-{i}", 30)
        except GroupError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(add, range(6)))
    accepted = [item for item in results if isinstance(item, dict)]
    assert len(accepted) == 5
    assert results.count("group_limit_reached") == 1
    gid = UUID(accepted[0]["id"])
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(groups)).scalar_one() == 5
        conn.rollback()
        schedule_due(conn, now)
    with pg_engine.connect() as conn:
        set_interval(conn, now, gid, 60)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(jobs.c.state).where(jobs.c.group_id == gid)).scalar_one()
            == "cancelled"
        )
        conn.rollback()
        schedule_due(conn, now + timedelta(hours=1))
    with pg_engine.connect() as conn:
        pause(conn, now + timedelta(hours=1), gid)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(
                select(func.count())
                .select_from(jobs)
                .where(jobs.c.group_id == gid, jobs.c.state == "queued")
            ).scalar_one()
            == 0
        )
    fresh = create_engine(pg_engine.url)
    try:
        with fresh.connect() as conn:
            row = conn.execute(select(groups).where(groups.c.id == gid)).mappings().one()
            assert (row["enabled"], row["poll_minutes"]) == (False, 60)
            assert (
                conn.execute(
                    select(func.count())
                    .select_from(capture_gaps)
                    .where(capture_gaps.c.group_id == gid)
                ).scalar_one()
                == 0
            )
            conn.rollback()
            resume(conn, now + timedelta(hours=2), gid)
        with fresh.connect() as conn:
            queued = (
                conn.execute(select(jobs).where(jobs.c.group_id == gid, jobs.c.state == "queued"))
                .mappings()
                .one()
            )
            assert queued["due_at"] == (now + timedelta(hours=2)).replace(
                minute=0, second=0, microsecond=0
            )
    finally:
        fresh.dispose()


def test_schedule_coalesces_and_lease_retries(pg_engine):
    now = datetime.now(UTC).replace(minute=10, second=0, microsecond=0)
    with pg_engine.connect() as conn:
        group = add_group(conn, now, "synthetic-queue", 30)
        gid = UUID(group["id"])
        first = schedule_due(conn, now)
        assert schedule_due(conn, now) == first
        new = schedule_due(conn, now + timedelta(minutes=90))
    with pg_engine.connect() as conn:
        rows = conn.execute(select(jobs).order_by(jobs.c.due_at)).mappings().all()
        assert [r["state"] for r in rows] == ["cancelled", "queued"]
        assert rows[0]["due_at"] == now.replace(minute=0)
        gap = conn.execute(select(capture_gaps)).mappings().one()
        assert gap["reason"] == "missed_slots" and gap["compensated_by_job_id"] == new[0]
    clock = now + timedelta(minutes=90)
    with pg_engine.connect() as conn:
        claim = claim_next(conn, clock)
    assert claim is not None
    run_id = claim.run_id
    with pg_engine.connect() as conn:
        assert claim_next(conn, clock) is None
    for action in (renew_lease, complete):
        with pg_engine.connect() as conn, pytest.raises(QueueError, match="lease"):
            action(conn, claim.job_id, uuid4(), clock)
    with pg_engine.connect() as conn:
        renew_lease(conn, claim.job_id, claim.lease_token, clock)
    for retry, backoff in enumerate((1, 2, 4), start=1):
        clock += timedelta(minutes=5)
        with pg_engine.connect() as conn:
            assert claim_next(conn, clock) is None
        with pg_engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == claim.job_id)).mappings().one()
            assert (row["state"], row["retry_count"], row["due_at"]) == (
                "queued",
                retry,
                clock + timedelta(minutes=backoff),
            )
        clock += timedelta(minutes=backoff)
        with pg_engine.connect() as conn:
            claim = claim_next(conn, clock)
        assert claim is not None and claim.run_id == run_id
    clock += timedelta(minutes=5)
    with pg_engine.connect() as conn:
        assert claim_next(conn, clock) is None
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(jobs.c.state).where(jobs.c.id == claim.job_id)).scalar_one()
            == "failed"
        )
        assert conn.execute(select(func.count()).select_from(capture_runs)).scalar_one() == 1
        assert (
            conn.execute(
                select(capture_gaps.c.reason).where(
                    capture_gaps.c.group_id == gid, capture_gaps.c.reason == "run_failed"
                )
            ).scalar_one()
            == "run_failed"
        )


def test_revoked_grant_cancels_claim(pg_engine):
    now = datetime.now(UTC)
    with pg_engine.connect() as conn:
        group = add_group(conn, now, "synthetic-revoked", 30)
        gid = UUID(group["id"])
        schedule_due(conn, now)
    with pg_engine.begin() as conn:
        conn.execute(
            update(source_grants).where(source_grants.c.group_id == gid).values(revoked_at=now)
        )
    with pg_engine.connect() as conn:
        assert claim_next(conn, now) is None
    with pg_engine.connect() as conn:
        assert conn.execute(select(jobs.c.state)).scalar_one() == "cancelled"
        assert conn.execute(select(capture_runs.c.error_code)).scalar_one() == "grant_unavailable"


def test_cli_json_lines(pg_engine):
    import json
    import os
    import subprocess
    import sys

    def call(*args):
        result = subprocess.run(
            [sys.executable, "-m", "paiya.cli", "groups", *args],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            check=False,
        )
        lines = result.stdout.splitlines()
        assert len(lines) == 1
        return result.returncode, json.loads(lines[0])

    code, added = call("add", "synthetic-cli", "--poll-minutes", "30")
    assert code == 0 and added["ok"]
    gid = added["result"]["id"]
    for args in (("set-interval", gid, "60"), ("pause", gid), ("resume", gid), ("list",)):
        code, payload = call(*args)
        assert code == 0 and payload["ok"]
    code, payload = call("add", "real-source", "--poll-minutes", "30")
    assert code != 0 and payload["error"]["code"] == "invalid_group_key"


def test_runs_api_pagination_and_scope(pg_engine):
    from collections.abc import Iterator

    from fastapi.testclient import TestClient
    from sqlalchemy import Connection

    from paiya.api.app import app
    from paiya.api.evidence import get_db_connection, require_fixture_scope
    from paiya.store.fixture_scope import FixtureScope

    now = datetime.now(UTC) - timedelta(minutes=1)
    with pg_engine.connect() as conn:
        group = add_group(conn, now, "synthetic-api", 30)
        gid = UUID(group["id"])
        grant = conn.execute(
            select(source_grants.c.id).where(source_grants.c.group_id == gid)
        ).scalar_one()
    scope = FixtureScope(gid, grant, "synthetic-api")
    for i in range(23):
        clock = now + timedelta(minutes=30 * i)
        with pg_engine.connect() as conn:
            schedule_due(conn, clock)
        with pg_engine.connect() as conn:
            claim = claim_next(conn, clock)
        with pg_engine.connect() as conn:
            complete(conn, claim.job_id, claim.lease_token, clock)

    from sqlalchemy import insert

    from paiya.store.tables import sale_events

    with pg_engine.begin() as conn:
        conn.execute(
            insert(capture_gaps).values(
                id=uuid4(),
                group_id=gid,
                gap_from=now + timedelta(minutes=620),
                gap_to=now + timedelta(minutes=640),
                reason="missed_slots",
                detected_at=now,
            )
        )
        assert conn.execute(select(func.count()).select_from(sale_events)).scalar_one() == 0

    def db() -> Iterator[Connection]:
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    try:
        with TestClient(app) as client:
            assert client.get(f"/v1/groups/{gid}/runs").status_code == 401
            app.dependency_overrides[require_fixture_scope] = lambda: scope
            response = client.get(f"/v1/groups/{gid}/runs")
            assert response.status_code == 200
            body = response.json()
            assert set(body) == {"group_id", "runs", "gaps", "next_cursor"}
            assert len(body["runs"]) == 20 and body["next_cursor"]
            assert len(body["gaps"]) == 1
            assert set(body["gaps"][0]) == {"gap_from", "gap_to", "reason"}
            assert set(body["runs"][0]) == {
                "run_id",
                "job_kind",
                "scheduled_for",
                "started_at",
                "ended_at",
                "coverage",
                "partial_reasons",
                "stop_reason",
                "cards_seen",
                "cards_parsed",
                "session_result",
                "sort_result",
                "error_code",
            }
            second = client.get(
                f"/v1/groups/{gid}/runs", params={"cursor": body["next_cursor"]}
            ).json()
            all_runs = body["runs"] + second["runs"]
            assert len(all_runs) == len({r["run_id"] for r in all_runs}) == 23
            assert [r["started_at"] for r in all_runs] == sorted(
                (r["started_at"] for r in all_runs), reverse=True
            )
            assert (
                len(client.get(f"/v1/groups/{gid}/runs", params={"limit": 100}).json()["runs"])
                == 23
            )
            assert client.get(f"/v1/groups/{gid}/runs", params={"limit": 101}).status_code == 422
            other = client.get(f"/v1/groups/{uuid4()}/runs")
            assert other.status_code == 404 and other.json()["detail"]["code"] == "not_found"
    finally:
        app.dependency_overrides.clear()


def test_global_lease_excludes_other_group_and_owner_can_complete(pg_engine):
    now = datetime.now(UTC) - timedelta(minutes=1)
    with pg_engine.connect() as conn:
        add_group(conn, now, "synthetic-global-one", 30)
        add_group(conn, now, "synthetic-global-two", 60)
        schedule_due(conn, now)
    with pg_engine.connect() as conn:
        due = conn.execute(select(jobs.c.due_at).order_by(jobs.c.due_at)).scalars().all()
        assert due == [
            now.replace(minute=0, second=0, microsecond=0),
            now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0),
        ]
    with pg_engine.connect() as conn:
        first = claim_next(conn, now)
    with pg_engine.connect() as conn:
        assert claim_next(conn, now) is None
    with pg_engine.connect() as conn:
        complete(conn, first.job_id, first.lease_token, now)
    with pg_engine.connect() as conn:
        second = claim_next(conn, now)
    assert second is not None and second.job_id != first.job_id
