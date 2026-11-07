"""Synthetic watch service checks against PostgreSQL."""

import copy
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.exc import IntegrityError
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401

from paiya.collector.revisit_run import execute_revisit
from paiya.collector.revisit_script import RevisitScriptAdapter
from paiya.ingest.service import ingest_fixture
from paiya.read.service import read_watches
from paiya.scheduler.queue import claim_next, complete, schedule_due
from paiya.store.fixture_scope import seed_fixture_scope
from paiya.store.tables import (
    capture_runs,
    current_items,
    groups,
    jobs,
    observations,
    price_facts,
    sale_events,
    source_grants,
    watches,
)
from paiya.watches.errors import WatchError
from paiya.watches.service import add_watch, remove_watch, set_watch_hot

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


def make_posts(engine, tmp_path, count, name="service"):
    from test_a2_ingest import SAMPLE

    scope = seed_fixture_scope(engine, f"synthetic-watch-{name}")
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    ids = []
    for i in range(count):
        document = copy.deepcopy(sample)
        document["run_id"] = str(uuid4())
        document["capture_id"] = f"synthetic-watch-{name}-{i}"
        document["payload"]["group_key"] = scope.group_key
        document["payload"]["source_post_key"] = f"watch-post-{i}"
        oid = ingest_fixture(engine, scope, document, {}, tmp_path)
        with engine.connect() as conn:
            ids.append(
                conn.execute(
                    select(observations.c.post_id).where(observations.c.id == oid)
                ).scalar_one()
            )
    return scope, ids


def test_watch_idempotency_hot_cap_and_expiry(pg_engine, tmp_path):
    _, ids = make_posts(pg_engine, tmp_path, 11)
    with pg_engine.connect() as conn:
        first = add_watch(conn, NOW, ids[0])
        assert add_watch(conn, NOW + timedelta(hours=1), ids[0]) == first
        assert first["saved_at"] == NOW.isoformat()
        assert first["next_due_at"] == (NOW + timedelta(hours=24)).isoformat()
        for post_id in ids[1:]:
            add_watch(conn, NOW, post_id)
        for post_id in ids[:10]:
            assert (
                set_watch_hot(conn, NOW, post_id)["hot_until"]
                == (NOW + timedelta(hours=24)).isoformat()
            )
        with pytest.raises(WatchError) as error:
            set_watch_hot(conn, NOW, ids[10])
        assert error.value.code == "hot_watch_limit_reached"
        assert (
            conn.execute(
                select(watches.c.hot_until).where(watches.c.post_id == ids[10])
            ).scalar_one()
            is None
        )
        conn.rollback()
        assert (
            set_watch_hot(conn, NOW + timedelta(hours=24), ids[10])["hot_until"]
            == (NOW + timedelta(hours=48)).isoformat()
        )


def test_watch_rejects_revoked_group_and_cli_returns_one_json_line(pg_engine, tmp_path):
    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(
            update(source_grants).where(source_grants.c.id == scope.grant_id).values(revoked_at=NOW)
        )
    with pg_engine.connect() as conn, pytest.raises(WatchError) as error:
        add_watch(conn, NOW, ids[0])
    assert error.value.code == "grant_unavailable"
    result = subprocess.run(
        [sys.executable, "-m", "paiya.cli", "watches", "add", str(ids[0])],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    assert result.returncode != 0
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["error"]["code"] == "grant_unavailable"


def finish_discovery(engine, now):
    with engine.connect() as conn:
        schedule_due(conn, now)
    with engine.connect() as conn:
        claim = claim_next(conn, now)
    assert claim is not None and claim.kind == "discover"
    with engine.connect() as conn:
        assert claim_next(conn, now) is None
    with engine.connect() as conn:
        complete(conn, claim.job_id, claim.lease_token, now)


def test_budget_oldest_first_unique_index_and_discovery_priority(pg_engine, tmp_path):
    scope, ids = make_posts(pg_engine, tmp_path, 12)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
    with pg_engine.connect() as conn:
        for i, post_id in enumerate(ids):
            add_watch(conn, NOW - timedelta(days=3), post_id)
            conn.execute(
                update(watches)
                .where(watches.c.post_id == post_id)
                .values(next_due_at=NOW - timedelta(minutes=12 - i))
            )
            conn.commit()
    previous_job = uuid4()
    previous_run = uuid4()
    previous_success = NOW - timedelta(days=2)
    with pg_engine.begin() as conn:
        conn.execute(
            insert(jobs).values(
                id=previous_job,
                group_id=scope.group_id,
                grant_id=scope.grant_id,
                kind="revisit",
                target_key=str(ids[-1]),
                logical_key="synthetic-previous-revisit",
                due_at=previous_success,
                state="succeeded",
            )
        )
        conn.execute(
            insert(capture_runs).values(
                id=previous_run,
                job_id=previous_job,
                group_id=scope.group_id,
                grant_id=scope.grant_id,
                source_kind="fixture",
                started_at=previous_success,
                ended_at=previous_success,
                coverage="complete",
            )
        )
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    with pg_engine.connect() as conn:
        targets = set(
            conn.execute(
                select(jobs.c.target_key).where(jobs.c.kind == "revisit", jobs.c.state == "queued")
            ).scalars()
        )
    assert targets == {str(post_id) for post_id in ids[:10]}
    with pg_engine.connect() as conn:
        rows = read_watches(conn, scope, NOW)["watches"]
    delayed = next(row for row in rows if row["post_id"] == str(ids[-1]))
    assert delayed["overdue_since"] == delayed["next_due_at"]
    assert datetime.fromisoformat(delayed["last_success_at"]) == previous_success
    with pytest.raises(IntegrityError), pg_engine.begin() as conn:
        conn.execute(
            insert(jobs).values(
                id=uuid4(),
                group_id=scope.group_id,
                grant_id=scope.grant_id,
                kind="revisit",
                target_key=str(ids[0]),
                logical_key="duplicate-revisit",
                due_at=NOW,
                state="queued",
            )
        )
    later = NOW + timedelta(minutes=30)
    with pg_engine.connect() as conn:
        schedule_due(conn, later)
    with pg_engine.connect() as conn:
        claim = claim_next(conn, later)
    assert claim is not None and claim.kind == "discover"
    with pg_engine.connect() as conn:
        complete(conn, claim.job_id, claim.lease_token, later)
    with pg_engine.connect() as conn:
        schedule_due(conn, later)
    with pg_engine.connect() as conn:
        targets = set(
            conn.execute(
                select(jobs.c.target_key).where(jobs.c.kind == "revisit", jobs.c.state == "queued")
            ).scalars()
        )
    assert targets == {str(post_id) for post_id in ids}


def test_remove_cancels_queued_slow_revisit(pg_engine, tmp_path):
    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
    with pg_engine.connect() as conn:
        add_watch(conn, NOW - timedelta(days=2), ids[0])
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    with pg_engine.connect() as conn:
        result = remove_watch(conn, NOW, ids[0])
        assert result["cancelled_revisit"] is True
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(jobs.c.state).where(jobs.c.kind == "revisit")).scalar_one()
            == "cancelled"
        )


def test_remove_preserves_revisit_when_post_is_still_new(pg_engine, tmp_path):
    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
        conn.execute(
            update(observations)
            .where(observations.c.post_id == ids[0])
            .values(observed_at=NOW - timedelta(hours=1))
        )
    with pg_engine.connect() as conn:
        add_watch(conn, NOW, ids[0])
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    with pg_engine.connect() as conn:
        assert remove_watch(conn, NOW, ids[0])["cancelled_revisit"] is False
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(jobs.c.state).where(jobs.c.kind == "revisit")).scalar_one()
            == "queued"
        )


def test_active_revisit_is_not_reinserted_during_a_later_hot_slot(pg_engine, tmp_path):
    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
    with pg_engine.connect() as conn:
        add_watch(conn, NOW, ids[0])
        set_watch_hot(conn, NOW, ids[0])
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    inserts = []

    def record_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into jobs") and "revisit" in str(parameters):
            inserts.append(statement)

    event.listen(pg_engine, "before_cursor_execute", record_insert)
    try:
        with pg_engine.connect() as conn:
            schedule_due(conn, NOW + timedelta(minutes=10))
    finally:
        event.remove(pg_engine, "before_cursor_execute", record_insert)
    assert not inserts
    with pg_engine.connect() as conn:
        assert (
            len(
                conn.execute(
                    select(jobs.c.id).where(
                        jobs.c.kind == "revisit", jobs.c.state.in_(("queued", "leased"))
                    )
                ).all()
            )
            == 1
        )


def test_hot_slots_stop_after_expiry_while_discovery_continues(pg_engine, tmp_path):
    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
    with pg_engine.connect() as conn:
        add_watch(conn, NOW, ids[0])
        set_watch_hot(conn, NOW, ids[0])
    finish_discovery(pg_engine, NOW)
    for minutes in (0, 10, 20):
        clock = NOW + timedelta(minutes=minutes)
        with pg_engine.connect() as conn:
            schedule_due(conn, clock)
        with pg_engine.connect() as conn:
            claim = claim_next(conn, clock)
        assert claim is not None and claim.kind == "revisit" and claim.due_at == clock
        with pg_engine.begin() as conn:
            conn.execute(
                update(capture_runs)
                .where(capture_runs.c.id == claim.run_id)
                .values(coverage="complete", ended_at=clock)
            )
            complete(conn, claim.job_id, claim.lease_token, clock)
    expiry = NOW + timedelta(hours=24)
    with pg_engine.begin() as conn:
        conn.execute(
            update(watches)
            .where(watches.c.post_id == ids[0])
            .values(next_due_at=expiry + timedelta(hours=1))
        )
    finish_discovery(pg_engine, expiry)
    with pg_engine.connect() as conn:
        schedule_due(conn, expiry + timedelta(minutes=10))
    with pg_engine.connect() as conn:
        assert (
            conn.execute(
                select(jobs.c.id).where(
                    jobs.c.kind == "revisit", jobs.c.state.in_(("queued", "leased"))
                )
            ).first()
            is None
        )


def test_new_unwatched_post_revisits_only_in_first_day(pg_engine, tmp_path):
    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
        conn.execute(
            update(observations).where(observations.c.post_id == ids[0]).values(observed_at=NOW)
        )
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
        assert conn.execute(select(jobs.c.id).where(jobs.c.kind == "revisit")).first() is None
    slot = NOW + timedelta(minutes=30)
    finish_discovery(pg_engine, slot)
    with pg_engine.connect() as conn:
        schedule_due(conn, slot)
    with pg_engine.connect() as conn:
        claim = claim_next(conn, slot)
    assert claim is not None and claim.kind == "revisit"
    with pg_engine.connect() as conn:
        complete(conn, claim.job_id, claim.lease_token, slot)
    end = NOW + timedelta(hours=24)
    finish_discovery(pg_engine, end)
    with pg_engine.connect() as conn:
        schedule_due(conn, end)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(
                select(jobs.c.id).where(
                    jobs.c.kind == "revisit", jobs.c.state.in_(("queued", "leased"))
                )
            ).first()
            is None
        )


@pytest.mark.parametrize("closed_status", ["sold", "withdrawn"])
def test_slow_revisit_stops_when_sold_and_resumes_after_reopen(pg_engine, tmp_path, closed_status):
    from test_a2_ingest import SAMPLE

    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
    sold = json.loads(SAMPLE.read_text(encoding="utf-8"))
    sold["run_id"] = str(uuid4())
    sold["capture_id"] = "synthetic-sold"
    sold["payload"].update(
        group_key=scope.group_key,
        source_post_key="watch-post-0",
        observed_at="2026-11-07T10:00:00Z",
    )
    sold["payload"]["items"][0]["source_status_label"] = "ขายแล้ว"
    sold["payload"]["evidence"][0]["source_span"]["quote"] = "RTX 3060 ขายแล้ว"
    ingest_fixture(pg_engine, scope, sold, {}, tmp_path)
    if closed_status == "withdrawn":
        # A4a does not map a withdrawn label yet; seed the supported projection state.
        with pg_engine.begin() as conn:
            conn.execute(
                update(sale_events)
                .where(sale_events.c.post_id == ids[0], sale_events.c.sale_status == "sold")
                .values(sale_status="withdrawn")
            )
    with pg_engine.connect() as conn:
        add_watch(conn, NOW - timedelta(days=2), ids[0])
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    with pg_engine.connect() as conn:
        assert conn.execute(select(jobs.c.id).where(jobs.c.kind == "revisit")).first() is None
    reopened = copy.deepcopy(sold)
    reopened["run_id"] = str(uuid4())
    reopened["capture_id"] = "synthetic-reopened"
    reopened["payload"]["observed_at"] = "2026-11-07T11:00:00Z"
    reopened["payload"]["items"][0]["source_status_label"] = "เปิดขาย"
    reopened["payload"]["evidence"][0]["source_span"]["quote"] = "RTX 3060 เปิดขาย"
    ingest_fixture(pg_engine, scope, reopened, {}, tmp_path)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(jobs.c.state).where(jobs.c.kind == "revisit")).scalar_one()
            == "queued"
        )


def test_revoked_grant_cancels_queued_revisit_on_claim(pg_engine, tmp_path):
    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
    with pg_engine.connect() as conn:
        add_watch(conn, NOW - timedelta(days=2), ids[0])
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    with pg_engine.begin() as conn:
        conn.execute(
            update(source_grants).where(source_grants.c.id == scope.grant_id).values(revoked_at=NOW)
        )
    with pg_engine.connect() as conn:
        assert claim_next(conn, NOW) is None
    with pg_engine.connect() as conn:
        row = conn.execute(
            select(jobs.c.state, capture_runs.c.error_code)
            .join(capture_runs, capture_runs.c.job_id == jobs.c.id)
            .where(jobs.c.kind == "revisit")
        ).one()
        assert row == ("cancelled", "grant_unavailable")


def test_watches_api_is_scoped_and_reports_overdue(pg_engine, tmp_path, monkeypatch):
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

    import paiya.api.evidence as api
    from paiya.api.app import app

    scope, ids = make_posts(pg_engine, tmp_path, 1)
    _, other_ids = make_posts(pg_engine, tmp_path, 1, name="other")
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
    with pg_engine.connect() as conn:
        add_watch(conn, NOW - timedelta(days=2), ids[0])
        add_watch(conn, NOW - timedelta(days=2), other_ids[0])
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    with pg_engine.connect() as conn:
        claim = claim_next(conn, NOW)
    assert claim is not None and claim.kind == "revisit"
    with pg_engine.begin() as conn:
        conn.execute(
            update(capture_runs)
            .where(capture_runs.c.id == claim.run_id)
            .values(ended_at=NOW, coverage="complete")
        )
        complete(conn, claim.job_id, claim.lease_token, NOW)
        conn.execute(
            update(watches)
            .where(watches.c.post_id == ids[0])
            .values(next_due_at=NOW + timedelta(days=1))
        )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(days=2)

    def db() -> Iterator:
        with pg_engine.connect() as conn:
            yield conn

    monkeypatch.setattr(api, "datetime", Clock)
    app.dependency_overrides[api.get_db_connection] = db
    try:
        with TestClient(app) as client:
            assert client.get("/v1/watches").status_code == 401
            app.dependency_overrides[api.require_read_context] = lambda: scope
            response = client.get("/v1/watches")
            assert response.status_code == 200
            rows = response.json()["watches"]
            assert len(rows) == 1 and rows[0]["post_id"] == str(ids[0])
            assert set(rows[0]) == {
                "post_id",
                "saved_at",
                "hot_until",
                "next_due_at",
                "last_success_at",
                "overdue_since",
            }
            assert datetime.fromisoformat(rows[0]["last_success_at"]) == NOW
            assert datetime.fromisoformat(rows[0]["overdue_since"]) == NOW + timedelta(days=1)
            assert rows[0]["overdue_since"] == rows[0]["next_due_at"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "access,error",
    [
        ("ok", None),
        ("unavailable", "post_unavailable"),
        ("permission_denied", "permission_denied"),
        ("session_required", "session_required"),
        ("challenge_required", "challenge_required"),
    ],
)
def test_revisit_executor_records_access_and_preserves_or_updates_status(
    pg_engine, tmp_path, access, error
):
    from test_a2_ingest import SAMPLE

    scope, ids = make_posts(pg_engine, tmp_path, 1)
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=30))
    with pg_engine.connect() as conn:
        add_watch(conn, NOW - timedelta(days=2), ids[0])
    finish_discovery(pg_engine, NOW)
    with pg_engine.connect() as conn:
        schedule_due(conn, NOW)
    with pg_engine.connect() as conn:
        claim = claim_next(conn, NOW)
    assert claim is not None and claim.kind == "revisit"
    reserved = json.loads((SAMPLE.parent / "02-reserved.json").read_text(encoding="utf-8"))
    reserved["payload"]["group_key"] = scope.group_key
    reserved["payload"]["source_post_key"] = "watch-post-0"
    (tmp_path / "envelope.json").write_text(json.dumps(reserved), encoding="utf-8")
    script = {
        "revisit_version": 1,
        "group_key": scope.group_key,
        "visits": [
            {
                "source_post_key": "watch-post-0",
                "at": claim.due_at.isoformat().replace("+00:00", "Z"),
                "access": access,
                "envelope": "envelope.json" if access == "ok" else None,
            }
        ],
    }
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    result = execute_revisit(
        pg_engine, scope, claim, RevisitScriptAdapter(path), NOW, media_root=tmp_path
    )
    assert result.error_code == error
    assert result.coverage == ("complete" if access == "ok" else "none")
    with pg_engine.connect() as conn:
        run = (
            conn.execute(select(capture_runs).where(capture_runs.c.id == claim.run_id))
            .mappings()
            .one()
        )
        assert (run["sort_result"], run["coverage"], run["error_code"]) == (
            "not_applicable",
            result.coverage,
            error,
        )
        assert (
            conn.execute(
                select(observations.c.access).where(observations.c.id == result.observation_id)
            ).scalar_one()
            == access
        )
        envelope = conn.execute(
            select(observations.c.normalized_json).where(observations.c.id == result.observation_id)
        ).scalar_one()
        assert envelope["job_kind"] == "revisit"
        assert envelope["page_check"] == {"session": "ok", "sort": "not_applicable"}
        assert (
            conn.execute(select(jobs.c.kind).where(jobs.c.id == claim.job_id)).scalar_one()
            == "revisit"
        )
        status = conn.execute(
            select(sale_events.c.sale_status)
            .join(current_items, current_items.c.status_event_id == sale_events.c.id)
            .where(current_items.c.post_id == ids[0])
        ).scalar_one()
        assert status == ("reserved" if access == "ok" else "open")
        prices = (
            conn.execute(
                select(price_facts.c.amount).where(
                    price_facts.c.observation_id == result.observation_id
                )
            )
            .scalars()
            .all()
        )
        assert prices == ([5500] if access == "ok" else [])
        due = conn.execute(
            select(watches.c.next_due_at).where(watches.c.post_id == ids[0])
        ).scalar_one()
        assert due == (NOW + timedelta(days=1) if access == "ok" else NOW - timedelta(days=1))
    if access == "ok":
        with pg_engine.connect() as conn:
            schedule_due(conn, NOW + timedelta(minutes=10))
        with pg_engine.connect() as conn:
            assert (
                conn.execute(
                    select(jobs.c.id).where(jobs.c.kind == "revisit", jobs.c.state == "queued")
                ).first()
                is None
            )
        next_day = NOW + timedelta(days=1)
        finish_discovery(pg_engine, next_day)
        with pg_engine.connect() as conn:
            schedule_due(conn, next_day)
            schedule_due(conn, next_day)
        with pg_engine.connect() as conn:
            assert (
                len(
                    conn.execute(
                        select(jobs.c.id).where(jobs.c.kind == "revisit", jobs.c.state == "queued")
                    ).all()
                )
                == 1
            )
