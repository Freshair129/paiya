"""Synthetic price trend contract checks."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text, update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a4c_seller import envelope

from paiya import serve
from paiya.api import prices as price_api
from paiya.api.app import app
from paiya.api.evidence import get_db_connection, require_fixture_scope
from paiya.ingest.service import ingest_fixture
from paiya.prices.trends import _TREND_SQL, compute_price_trend
from paiya.read.service import read_post, read_post_timeline
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.tables import (
    current_items,
    evidence,
    groups,
    jobs,
    listing_items,
    observations,
    posts,
    price_facts,
    source_grants,
)

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def add_ask(
    engine, scope, path, key, at, amount="100", evidence_role="seller", actor=None, **price
):
    doc = envelope(scope, key, actor=actor or key)
    doc["payload"]["observed_at"] = at
    doc["payload"]["evidence"][0]["role"] = evidence_role
    doc["payload"]["items"][0]["price"] = {
        "kind": "ask",
        "unit": "each",
        "currency": "THB",
        "amount": amount,
        **price,
    }
    oid = ingest_fixture(engine, scope, doc, {}, path)
    with engine.begin() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
        conn.execute(
            update(listing_items).where(listing_items.c.post_id == post_id).values(model_key="gpu")
        )
    return doc


def trend(engine, scope, now=NOW):
    with engine.connect() as conn:
        return compute_price_trend(conn, scope, {"model_key": "gpu"}, now)


def test_first_ask_repoll_and_replay(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "1")
    monkeypatch.setenv("PAIYA_TREND_MIN_SELLERS", "1")
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-replay")
    doc = add_ask(pg_engine, scope, tmp_path, "post", "2026-11-05T10:00:00Z")
    before = trend(pg_engine, scope)
    assert before["current"]["n"] == 1
    assert before["current"]["median"] == "100.00"
    add_ask(pg_engine, scope, tmp_path, "post", "2026-11-06T10:00:00Z", "50")
    assert trend(pg_engine, scope)["current"] == before["current"]
    ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    assert trend(pg_engine, scope)["current"] == before["current"]


@pytest.mark.parametrize("name", ["WINDOW_DAYS", "MIN_OFFERS", "MIN_SELLERS", "FLAT_BAND_PCT"])
@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_invalid_settings_stop_serve(monkeypatch, name, value):
    monkeypatch.setenv(f"PAIYA_TREND_{name}", value)
    with patch.object(serve.uvicorn, "run") as run, pytest.raises(SystemExit) as error:
        serve.main()
    assert error.value.code == 2
    run.assert_not_called()


def test_window_settings_and_bangkok_midnight(pg_engine, monkeypatch):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-window")
    monkeypatch.setenv("PAIYA_TREND_WINDOW_DAYS", "3")
    before = trend(pg_engine, scope, datetime(2026, 11, 7, 16, 59, tzinfo=UTC))
    after = trend(pg_engine, scope, datetime(2026, 11, 7, 17, 0, tzinfo=UTC))
    assert before["window"]["current"] == {
        "from": "2026-11-03T17:00:00Z",
        "to": "2026-11-06T17:00:00Z",
    }
    assert after["window"]["current"] == {
        "from": "2026-11-04T17:00:00Z",
        "to": "2026-11-07T17:00:00Z",
    }
    assert after["settings"]["window_days"] == 3


def test_window_edges_and_excluded_units_ranges(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "1")
    monkeypatch.setenv("PAIYA_TREND_MIN_SELLERS", "1")
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-edges")
    add_ask(pg_engine, scope, tmp_path, "from", "2026-10-24T17:00:00Z")
    add_ask(pg_engine, scope, tmp_path, "to", "2026-11-07T17:00:00Z")
    add_ask(pg_engine, scope, tmp_path, "bundle", "2026-11-05T00:00:00Z", unit="bundle")
    add_ask(pg_engine, scope, tmp_path, "range", "2026-11-05T00:00:00Z", amount_max="200")
    result = trend(pg_engine, scope)
    assert result["previous"]["n"] == 1
    assert result["current"]["n"] == 0
    assert result["excluded"] == {
        "unclassified": 0,
        "non_ask": 0,
        "bundle_or_unknown_unit": 1,
        "range": 1,
        "unconfirmed": 0,
    }


def test_rejected_ask_is_not_a_sample(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "1")
    monkeypatch.setenv("PAIYA_TREND_MIN_SELLERS", "1")
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-rejected")
    add_ask(pg_engine, scope, tmp_path, "rejected", "2026-11-05T00:00:00Z", evidence_role="other")
    result = trend(pg_engine, scope)
    assert result["current"]["n"] == 0
    assert result["excluded"]["unconfirmed"] == 1


def test_rejected_ask_remains_in_current_projection_and_timeline(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-rejected-projection")
    add_ask(pg_engine, scope, tmp_path, "post", "2026-11-05T00:00:00Z", "100")
    add_ask(pg_engine, scope, tmp_path, "post", "2026-11-06T00:00:00Z", "50", evidence_role="other")
    with pg_engine.connect() as conn:
        post_id = conn.execute(select(posts.c.id)).scalar_one()
        latest_id = conn.execute(
            select(price_facts.c.id).where(price_facts.c.validation_result == "rejected")
        ).scalar_one()
        assert conn.execute(select(current_items.c.ask_fact_id)).scalar_one() == latest_id
        assert read_post(conn, scope, post_id)["current_items"][0]["ask"]["amount"] == "50.00"
        events = read_post_timeline(conn, scope, post_id)["events"]
        assert [(e["from"], e["to"]) for e in events if e["kind"] == "price_changed"] == [
            ("100.00", "50.00")
        ]


def evidence_document(scope, case):
    path = ROOT / "fixtures/scenarios/S03-multi-item/rounds/03-seller-reserves-3060.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    payload = doc["payload"]
    payload["group_key"] = scope.group_key
    payload["items"] = payload["items"][:1]
    if case == "source_label":
        payload["evidence"][1]["role"] = "source_label"
    elif case == "post_text":
        payload["evidence"][1]["source_span"] = {"field": "post_text", "quote": "synthetic"}
    elif case == "role":
        payload["evidence"][0]["role"] = "seller"
        payload["items"][0]["evidence_keys"] = ["s03-buyer-text"]
    elif case == "actor":
        payload["comments"][1]["actor_key"] = "different-synthetic-actor"
    elif case == "seller":
        payload["seller"] = None
    elif case == "other":
        payload["evidence"][0]["source_span"] = {"field": "post_text", "quote": "synthetic"}
        payload["items"][0]["evidence_keys"] = ["s03-buyer-text"]
    elif case == "later_eligible":
        payload["items"][0]["evidence_keys"] = ["s03-buyer-text", "s03-02-3060"]
    return doc


@pytest.mark.parametrize(
    "case,expected,reason",
    [
        ("source_label", "confirmed", None),
        ("post_text", "confirmed", None),
        ("comment", "confirmed", None),
        ("role", "rejected", "role_mismatch"),
        ("actor", "rejected", "actor_mismatch"),
        ("seller", "rejected", "seller_unknown"),
        ("other", "rejected", "no_eligible_evidence"),
        ("later_eligible", "confirmed", None),
    ],
)
def test_price_evidence_validation(pg_engine, tmp_path, case, expected, reason):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-evidence")
    doc = evidence_document(scope, case)
    payload = doc["payload"]
    oid = ingest_fixture(pg_engine, scope, doc, {}, tmp_path)
    with pg_engine.connect() as conn:
        fact = conn.execute(
            select(
                price_facts.c.validation_result,
                price_facts.c.reason,
                price_facts.c.rule_version,
                evidence.c.local_key,
            )
            .join(evidence, evidence.c.id == price_facts.c.evidence_id)
            .where(price_facts.c.observation_id == oid)
        ).one()
    assert fact == (expected, reason, "price-v1.0", payload["items"][0]["evidence_keys"][0])


def test_backfill_matches_ingest_evidence_paths(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-backfill")
    cases = [
        "source_label",
        "post_text",
        "comment",
        "role",
        "actor",
        "seller",
        "other",
        "later_eligible",
    ]
    ids = []
    for case in cases:
        doc = evidence_document(scope, case)
        doc["run_id"] = str(uuid4())
        doc["capture_id"] = f"synthetic-{uuid4()}"
        doc["payload"]["source_post_key"] = f"synthetic-{case}"
        ids.append(ingest_fixture(pg_engine, scope, doc, {}, tmp_path))
    with pg_engine.connect() as conn:
        expected = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                select(
                    price_facts.c.observation_id,
                    price_facts.c.validation_result,
                    price_facts.c.reason,
                )
            ).all()
        }
    alembic = Config(str(ROOT / "alembic.ini"))
    command.downgrade(alembic, "0005_watches")
    copies = {}
    with pg_engine.begin() as conn:
        for observation_id in ids:
            new_id = uuid4()
            copies[new_id] = observation_id
            conn.execute(
                text("""
                INSERT INTO price_facts (id, observation_id, post_id, item_id, evidence_id,
                    fact_key, price_kind, unit, amount, amount_max, currency)
                SELECT :new_id, observation_id, post_id, item_id, evidence_id, 'legacy-copy',
                    price_kind, unit, amount, amount_max, currency
                FROM price_facts WHERE observation_id = :observation_id
            """),
                {"new_id": new_id, "observation_id": observation_id},
            )
    command.upgrade(alembic, "head")
    with pg_engine.connect() as conn:
        for copy_id, observation_id in copies.items():
            row = conn.execute(
                select(
                    price_facts.c.validation_result,
                    price_facts.c.reason,
                    price_facts.c.rule_version,
                ).where(price_facts.c.id == copy_id)
            ).one()
            assert row == (*expected[observation_id], "price-v1.0")


def test_same_group_second_grant_is_isolated(pg_engine, tmp_path):
    a = seed_fixture_scope(pg_engine, "synthetic-trend-grant")
    b = FixtureScope(a.group_id, uuid4(), a.group_key)
    with pg_engine.begin() as conn:
        conn.execute(
            insert(source_grants).values(
                id=b.grant_id,
                group_id=b.group_id,
                source_kind="fixture",
                scopes=["read", "capture", "media", "comments"],
                authority_ref="synthetic-second",
                valid_from=datetime.now(UTC),
                retention_days=30,
            )
        )
    add_ask(pg_engine, b, tmp_path, "other-grant", "2026-11-05T00:00:00Z")
    assert trend(pg_engine, a)["current"]["n"] == 0
    assert trend(pg_engine, b)["current"]["n"] == 1

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    try:
        with patch.object(price_api, "datetime", FixedDateTime), TestClient(app) as client:
            for scope, count in [(a, 0), (b, 1)]:
                app.dependency_overrides[require_fixture_scope] = lambda: scope
                response = client.get("/v1/prices/trends", params={"model_key": "gpu"})
                assert response.status_code == 200
                assert response.json()["current"]["n"] == count
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "count,sellers,status",
    [(9, 3, "insufficient_data"), (10, 2, "insufficient_data"), (10, 3, "ok")],
)
def test_thresholds(pg_engine, tmp_path, count, sellers, status):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-threshold")
    for i in range(count):
        add_ask(
            pg_engine,
            scope,
            tmp_path,
            f"post-{i}",
            "2026-11-05T00:00:00Z",
            actor=f"seller-{i % sellers}",
        )
    current = trend(pg_engine, scope)["current"]
    assert (current["n"], current["sellers"], current["status"]) == (count, sellers, status)
    if status == "insufficient_data":
        assert (current["median"], current["p25"], current["p75"]) == (None, None, None)


def test_null_seller_counts_in_n_only(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-no-seller")
    add_ask(pg_engine, scope, tmp_path, "post", "2026-11-05T00:00:00Z")
    with pg_engine.begin() as conn:
        conn.execute(update(posts).values(seller_id=None))
    result = trend(pg_engine, scope)["current"]
    assert result["n"] == 1 and result["sellers"] == 0


def test_even_percentiles_and_unrounded_median(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "2")
    monkeypatch.setenv("PAIYA_TREND_MIN_SELLERS", "1")
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-percentiles")
    for key, at, amount in [
        ("prev-1", "2026-10-28T00:00:00Z", "100.00"),
        ("prev-2", "2026-10-28T00:00:00Z", "100.01"),
        ("curr-1", "2026-11-05T00:00:00Z", "105.00"),
        ("curr-2", "2026-11-05T00:00:00Z", "105.01"),
    ]:
        add_ask(pg_engine, scope, tmp_path, key, at, amount)
    result = trend(pg_engine, scope)
    assert (result["previous"]["median"], result["previous"]["p25"], result["previous"]["p75"]) == (
        "100.01",
        "100.00",
        "100.01",
    )
    assert result["current"]["median"] == "105.01"
    assert result["change_pct"] == "5.00" and result["direction"] == "up"


def test_first_confirmed_outside_window_is_ignored(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-old-first")
    add_ask(pg_engine, scope, tmp_path, "post", "2026-10-20T00:00:00Z")
    add_ask(pg_engine, scope, tmp_path, "post", "2026-11-05T00:00:00Z", "50")
    result = trend(pg_engine, scope)
    assert result["current"]["n"] == result["previous"]["n"] == 0
    assert sum(result["excluded"].values()) == 0


def test_zero_previous_median_suppresses_direction(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "1")
    monkeypatch.setenv("PAIYA_TREND_MIN_SELLERS", "1")
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-zero")
    add_ask(pg_engine, scope, tmp_path, "prev", "2026-10-28T00:00:00Z", "0")
    add_ask(pg_engine, scope, tmp_path, "curr", "2026-11-05T00:00:00Z", "1")
    result = trend(pg_engine, scope)
    assert result["direction_reason"] == "insufficient_data"
    assert result["direction"] is None and result["change_pct"] is None


@pytest.mark.parametrize("kind", ["deposit", "shipping", "offer", "installment"])
def test_non_ask_price_is_excluded(pg_engine, tmp_path, kind):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-non-ask")
    add_ask(pg_engine, scope, tmp_path, "post", "2026-11-05T00:00:00Z", kind=kind)
    result = trend(pg_engine, scope)
    assert result["current"]["n"] == 0
    assert result["excluded"]["non_ask"] == 1


def test_unclassified_precedes_filters(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-unclassified")
    add_ask(pg_engine, scope, tmp_path, "post", "2026-11-05T00:00:00Z", unit="bundle")
    with pg_engine.begin() as conn:
        conn.execute(update(listing_items).values(model_key=None, variant="other"))
    result = trend(pg_engine, scope)
    assert result["excluded"]["unclassified"] == 1
    assert sum(result["excluded"].values()) == 1


def test_filter_and_currency_mismatches_are_ignored(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-mismatch")
    add_ask(pg_engine, scope, tmp_path, "other-model", "2026-11-05T00:00:00Z")
    with pg_engine.begin() as conn:
        conn.execute(update(listing_items).values(model_key="other"))
    add_ask(pg_engine, scope, tmp_path, "other-currency", "2026-11-05T00:00:00Z", currency="USD")
    result = trend(pg_engine, scope)
    assert result["current"]["n"] == 0
    assert sum(result["excluded"].values()) == 0


def test_coverage_and_insufficient_data_precedence(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "1")
    monkeypatch.setenv("PAIYA_TREND_MIN_SELLERS", "1")
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-coverage")
    add_ask(pg_engine, scope, tmp_path, "previous", "2026-10-28T00:00:00Z")
    add_ask(pg_engine, scope, tmp_path, "current", "2026-11-05T00:00:00Z")
    assert trend(pg_engine, scope)["direction"] == "flat"
    with pg_engine.begin() as conn:
        conn.execute(update(groups).where(groups.c.id == scope.group_id).values(poll_minutes=60))
    result = trend(pg_engine, scope)
    assert result["direction_reason"] == "coverage_differs"
    assert result["direction"] is None and result["change_pct"] is None
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "2")
    assert trend(pg_engine, scope)["direction_reason"] == "insufficient_data"


@pytest.mark.parametrize(
    "previous,value,expected",
    [
        ("100", "105", "up"),
        ("100", "95", "down"),
        ("100", "104.99", "flat"),
        ("1000", "1049.95", "up"),
    ],
)
def test_direction_uses_rounded_percentage(
    pg_engine, tmp_path, monkeypatch, previous, value, expected
):
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "1")
    monkeypatch.setenv("PAIYA_TREND_MIN_SELLERS", "1")
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-direction")
    add_ask(pg_engine, scope, tmp_path, "prev", "2026-10-28T00:00:00Z", previous)
    add_ask(pg_engine, scope, tmp_path, "curr", "2026-11-05T00:00:00Z", value)
    result = trend(pg_engine, scope)
    assert result["direction"] == expected
    assert isinstance(result["change_pct"], str)


def test_api_contract_and_grant(pg_engine, monkeypatch):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-api")
    monkeypatch.delenv("PAIYA_READ_CONTEXT_GROUP_KEY", raising=False)

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    try:
        with TestClient(app) as client:
            path = "/v1/prices/trends"
            assert client.get(path, params={"model_key": "gpu"}).status_code == 401
            app.dependency_overrides[require_fixture_scope] = lambda: scope
            for params in (
                {},
                {"model_key": "gpu", "unit": "bundle"},
                {"model_key": "gpu", "vram_gb": "abc"},
            ):
                assert client.get(path, params=params).status_code == 422
            response = client.get(path, params={"model_key": "gpu"})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            result = response.json()
            assert set(result) == {
                "label",
                "filters",
                "window",
                "current",
                "previous",
                "direction",
                "direction_reason",
                "change_pct",
                "excluded",
                "limitations",
                "rule_version",
                "settings",
            }
            assert result["filters"] == {
                "model_key": "gpu",
                "variant": None,
                "vram_gb": None,
                "condition": None,
                "unit": "each",
                "currency": "THB",
            }
            with pg_engine.begin() as conn:
                conn.execute(
                    update(source_grants)
                    .where(source_grants.c.id == scope.grant_id)
                    .values(revoked_at=datetime.now(UTC))
                )
            assert client.get(path, params={"model_key": "gpu"}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_optional_comparators_match_null_only(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-null-filters")
    for key in ("null", "variant", "vram", "condition"):
        add_ask(pg_engine, scope, tmp_path, key, "2026-11-05T00:00:00Z")
    with pg_engine.begin() as conn:
        for key, values in (
            ("variant", {"variant": "OC"}),
            ("vram", {"vram_gb": 8}),
            ("condition", {"condition": "used"}),
        ):
            conn.execute(
                update(listing_items)
                .where(
                    listing_items.c.post_id
                    == select(posts.c.id).where(posts.c.source_post_key == key).scalar_subquery()
                )
                .values(**values)
            )
        assert compute_price_trend(conn, scope, {"model_key": "gpu"}, NOW)["current"]["n"] == 1
        assert (
            compute_price_trend(conn, scope, {"model_key": "gpu", "variant": "OC"}, NOW)["current"][
                "n"
            ]
            == 1
        )


def test_coverage_exact_half_and_invalid_jobs(pg_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PAIYA_TREND_WINDOW_DAYS", "1")
    monkeypatch.setenv("PAIYA_TREND_MIN_OFFERS", "1")
    monkeypatch.setenv("PAIYA_TREND_MIN_SELLERS", "1")
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-half-coverage")
    add_ask(pg_engine, scope, tmp_path, "prev", "2026-11-06T00:00:00Z")
    add_ask(pg_engine, scope, tmp_path, "curr", "2026-11-07T00:00:00Z")
    previous = datetime(2026, 11, 5, 17, tzinfo=UTC)
    current = previous + timedelta(days=1)

    def job(slot, *, state="succeeded", kind="discover", key=None):
        return dict(
            id=uuid4(),
            group_id=scope.group_id,
            grant_id=scope.grant_id,
            kind=kind,
            target_key="synthetic",
            state=state,
            due_at=slot,
            logical_key=key or f"discover:{scope.group_id}:{slot:%Y-%m-%dT%H:%M:%SZ}",
        )

    with pg_engine.begin() as conn:
        conn.execute(update(groups).values(poll_minutes=60))
        conn.execute(
            insert(jobs),
            [job(start + timedelta(hours=i)) for start in (previous, current) for i in range(12)],
        )
        assert compute_price_trend(conn, scope, {"model_key": "gpu"}, NOW)["direction"] == "flat"
        conn.execute(jobs.delete().where(jobs.c.logical_key == job(current)["logical_key"]))
        # None of these four records can replace the missing success.
        slot = current + timedelta(hours=12)
        conn.execute(
            insert(jobs),
            [
                job(slot, state="failed"),
                job(slot + timedelta(hours=1), kind="revisit"),
                job(slot, key="discover:malformed"),
                job(slot, key=f"discover:{uuid4()}:{slot:%Y-%m-%dT%H:%M:%SZ}"),
            ],
        )
        result = compute_price_trend(conn, scope, {"model_key": "gpu"}, NOW)
        assert result["direction_reason"] == "coverage_differs"
        assert result["direction"] is None
        conn.execute(
            update(jobs)
            .where(jobs.c.due_at < current, jobs.c.target_key == "synthetic")
            .values(state="failed")
        )
        assert (
            compute_price_trend(conn, scope, {"model_key": "gpu"}, NOW)["direction_reason"]
            == "coverage_differs"
        )


def test_scheduled_slots_non_aligned_boundary(pg_engine):
    scope = seed_fixture_scope(pg_engine, "synthetic-trend-45-minute-slots")
    previous = datetime(2026, 11, 5, 17, tzinfo=UTC)
    current = previous + timedelta(days=1)
    # Query the service's own slot CTE so the assertion covers its alignment calculation.
    ctes = _TREND_SQL.text.split("    successful_slots AS (")[0].rstrip().removesuffix(",")
    query = text(ctes + " SELECT period, scheduled_count FROM scheduled_slots ORDER BY period")
    with pg_engine.begin() as conn:
        conn.execute(update(groups).values(poll_minutes=45))
        rows = conn.execute(
            query,
            {
                "group_id": scope.group_id,
                "previous_from": previous,
                "current_from": current,
                "window_to": current + timedelta(days=1),
                "grant_id": scope.grant_id,
                "model_key": "gpu",
                "variant": None,
                "vram_gb": None,
                "condition": None,
                "currency": "THB",
            },
        ).all()
    assert rows == [("current", 32), ("previous", 32)]
