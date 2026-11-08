"""Synthetic item status acceptance against reviewed scenario answers."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401

from paiya.ingest.service import ingest_fixture
from paiya.read.service import read_post
from paiya.store.fixture_scope import seed_fixture_scope
from paiya.store.tables import (
    current_items,
    evidence,
    listing_items,
    observations,
    price_facts,
    sale_events,
    status_facts,
)

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "fixtures/scenarios"
RUNS = [
    (p, run)
    for p in sorted(SCENARIOS.glob("S*/expected.json"))
    for run in json.loads(p.read_text(encoding="utf-8"))["runs"]
]


def counts(conn):
    return tuple(
        conn.execute(select(func.count()).select_from(table)).scalar_one()
        for table in (price_facts, status_facts, sale_events, current_items)
    )


@pytest.mark.parametrize(
    "expected_path,run", RUNS, ids=[f"{p.parent.name}-{r['name']}" for p, r in RUNS]
)
def test_golden_replay(pg_engine, tmp_path, expected_path, run):
    scope = seed_fixture_scope(pg_engine, "synthetic-golden")
    ids = {}
    documents = {}
    for filename in run["arrival_order"]:
        document = json.loads(
            (expected_path.parent / "rounds" / filename).read_text(encoding="utf-8")
        )
        document["payload"]["group_key"] = scope.group_key
        documents[filename] = document
        ids[filename] = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(select(func.count()).select_from(observations)).scalar_one()
            == run["expect"]["observations"]
        )
        post_id = conn.execute(select(observations.c.post_id).limit(1)).scalar_one()
        result = read_post(conn, scope, post_id)
        items = {item["source_item_key"]: item for item in result["current_items"]}
        assert set(items) == set(run["expect"]["items"])
        for key, expected in run["expect"]["items"].items():
            item = items[key]
            assert item["sale_status"] == expected["current_status"]
            if expected["ask"] is None:
                assert item["ask"] is None
            else:
                assert Decimal(item["ask"]["amount"]) == Decimal(expected["ask"]["amount"])
                assert item["ask"]["currency"] == expected["ask"]["currency"]

            def evidence_id(ref):
                if ref is None:
                    return None
                return conn.execute(
                    select(evidence.c.id).where(
                        evidence.c.observation_id == ids[ref["round"]],
                        evidence.c.local_key == ref["local_key"],
                    )
                ).scalar_one()

            assert item["status_evidence_id"] == evidence_id(expected["status_evidence"])
            history = (
                conn.execute(
                    select(
                        sale_events.c.sale_status,
                        observations.c.observed_at,
                        sale_events.c.cause_key,
                        sale_events.c.seq,
                        evidence.c.id.label("evidence_id"),
                    )
                    .select_from(
                        sale_events.join(
                            status_facts, sale_events.c.status_fact_id == status_facts.c.id
                        )
                        .join(observations, status_facts.c.observation_id == observations.c.id)
                        .join(evidence, status_facts.c.evidence_id == evidence.c.id)
                    )
                    .where(sale_events.c.item_id == item["item_id"])
                    .order_by(sale_events.c.seq)
                )
                .mappings()
                .all()
            )
            assert len(history) == len(expected["status_history"])
            for index, (actual, wanted) in enumerate(
                zip(history, expected["status_history"], strict=True), 1
            ):
                assert actual["seq"] == index
                assert actual["sale_status"] == wanted["status"]
                assert (
                    actual["observed_at"].isoformat().replace("+00:00", "Z")
                    == wanted["observed_at"]
                )
                assert actual["evidence_id"] == evidence_id(wanted["evidence"])
                assert actual["cause_key"] == f"status:{actual['evidence_id']}"
        if "activity" in run["expect"]:
            assert result["observations"][-1]["activity"] == run["expect"]["activity"]
        before = counts(conn)
        events_before = conn.execute(
            select(sale_events.c.item_id, sale_events.c.seq, sale_events.c.cause_key).order_by(
                sale_events.c.item_id, sale_events.c.seq
            )
        ).all()
    for filename, document in documents.items():
        assert ingest_fixture(pg_engine, scope, document, {}, tmp_path) == ids[filename]
    with pg_engine.connect() as conn:
        assert counts(conn) == before
        assert (
            conn.execute(
                select(sale_events.c.item_id, sale_events.c.seq, sale_events.c.cause_key).order_by(
                    sale_events.c.item_id, sale_events.c.seq
                )
            ).all()
            == events_before
        )


def scenario(name):
    path = SCENARIOS / "S03-multi-item" / "rounds" / name
    return json.loads(path.read_text(encoding="utf-8"))


def ingest(pg_engine, scope, tmp_path, document):
    document["payload"]["group_key"] = scope.group_key
    return ingest_fixture(pg_engine, scope, document, {}, tmp_path)


@pytest.mark.parametrize(
    "filename,reason",
    [
        ("missing-permalink.json", "'permalink' is a required property"),
        ("missing-comment.json", "refers to missing comment"),
    ],
)
def test_sold_evidence_with_missing_source_link_is_rejected(pg_engine, tmp_path, filename, reason):
    scope = seed_fixture_scope(pg_engine, "synthetic-missing-source")
    path = ROOT / "fixtures/a9a-missing-source-link" / filename
    document = json.loads(path.read_text(encoding="utf-8"))
    with pytest.raises(ValueError) as error:
        ingest(pg_engine, scope, tmp_path, document)
    assert reason in str(error.value)
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(sale_events)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(observations)).scalar_one() == 0


@pytest.mark.parametrize(
    "case,reason",
    [("role", "role_mismatch"), ("actor", "actor_mismatch"), ("seller", "seller_unknown")],
)
def test_rejected_comment_evidence(pg_engine, tmp_path, case, reason):
    scope = seed_fixture_scope(pg_engine, "synthetic-rejection")
    document = scenario("03-seller-reserves-3060.json")
    payload = document["payload"]
    payload["items"] = payload["items"][:1]
    if case == "role":
        item_evidence = payload["evidence"][0].copy()
        item_evidence.update(local_key="mislabeled-buyer", role="seller")
        payload["evidence"].append(item_evidence)
        payload["items"][0]["evidence_keys"] = ["mislabeled-buyer"]
    elif case == "actor":
        payload["comments"][1]["actor_key"] = "different-synthetic-actor"
    else:
        payload["seller"] = None
    ingest(pg_engine, scope, tmp_path, document)
    with pg_engine.connect() as conn:
        fact = conn.execute(select(status_facts.c.validation_result, status_facts.c.reason)).one()
        assert fact == ("rejected", reason)
        assert conn.execute(select(func.count()).select_from(sale_events)).scalar_one() == 0


def test_unrecognized_label_and_no_evidence_create_no_facts(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-no-facts")
    document = scenario("01-open-both.json")
    document["payload"]["items"][0]["source_status_label"] = "unknown synthetic label"
    del document["payload"]["items"][1]["evidence_keys"]
    ingest(pg_engine, scope, tmp_path, document)
    with pg_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(status_facts)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(price_facts)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(sale_events)).scalar_one() == 0


def test_constraints_reject_invalid_facts_and_foreign_item_pointers(pg_engine, tmp_path):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    scope = seed_fixture_scope(pg_engine, "synthetic-constraints")
    ingest(pg_engine, scope, tmp_path, scenario("01-open-both.json"))
    with pg_engine.connect() as conn:
        fact_ids = (
            conn.execute(select(status_facts.c.id).order_by(status_facts.c.id)).scalars().all()
        )
        price_id = conn.execute(select(price_facts.c.id).limit(1)).scalar_one()
        item_id = conn.execute(
            select(current_items.c.item_id).where(current_items.c.status_fact_id == fact_ids[0])
        ).scalar_one()
    statements = [
        update(status_facts)
        .where(status_facts.c.id == fact_ids[0])
        .values(validation_result="invalid"),
        update(price_facts).where(price_facts.c.id == price_id).values(amount_max=0),
        update(current_items)
        .where(current_items.c.item_id == item_id)
        .values(status_fact_id=fact_ids[1]),
    ]
    for statement in statements:
        with pytest.raises(IntegrityError), pg_engine.begin() as conn:
            conn.execute(statement)


def test_failed_revisit_preserves_current_item(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-unavailable")
    initial = scenario("01-open-both.json")
    ingest(pg_engine, scope, tmp_path, initial)
    path = ROOT / "fixtures/contract/observation/valid/03-revisit-unavailable.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["source_post_key"] = initial["payload"]["source_post_key"]
    document["payload"]["permalink"] = initial["payload"]["permalink"]
    with pg_engine.connect() as conn:
        before = counts(conn)
        current = conn.execute(select(current_items).order_by(current_items.c.item_id)).all()
    ingest(pg_engine, scope, tmp_path, document)
    with pg_engine.connect() as conn:
        assert counts(conn) == before
        assert (
            conn.execute(select(current_items).order_by(current_items.c.item_id)).all() == current
        )


def api_post(pg_engine, scope, post_id):
    from collections.abc import Iterator

    from fastapi.testclient import TestClient
    from sqlalchemy import Connection

    from paiya.api.app import app
    from paiya.api.evidence import get_db_connection, require_fixture_scope

    def db() -> Iterator[Connection]:
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[require_fixture_scope] = lambda: scope
    app.dependency_overrides[get_db_connection] = db
    try:
        with TestClient(app) as client:
            response = client.get(f"/v1/posts/{post_id}")
            assert response.status_code == 200
            return response.json()
    finally:
        app.dependency_overrides.clear()


def test_api_current_items_shape_and_unresolved(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-api-items")
    path = ROOT / "fixtures/contract/observation/valid/02-multi-item-seller-reserves-one.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    oid = ingest(pg_engine, scope, tmp_path, document)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
    result = api_post(pg_engine, scope, post_id)
    assert result["has_unresolved_items"] is True
    items = result["current_items"]
    assert [item["source_item_key"] for item in items] == ["rtx3060", "rtx3070"]
    for item in items:
        assert set(item) == {
            "item_id",
            "source_item_key",
            "sale_status",
            "status_since",
            "status_evidence_id",
            "last_success_at",
            "needs_review",
            "model_key",
            "vram_gb",
            "condition",
            "warranty",
            "warranty_until",
            "warranty_months_left",
            "ask",
        }
        assert item["sale_status"] == "unknown"
        assert item["status_since"] is None and item["status_evidence_id"] is None
        assert set(item["ask"]) == {"amount", "currency", "unit", "evidence_id"}
        assert item["ask"]["currency"] == "THB"


def test_equal_effective_time_marks_review_and_api(pg_engine, tmp_path):
    from uuid import uuid4

    scope = seed_fixture_scope(pg_engine, "synthetic-review")
    first = scenario("03-seller-reserves-3060.json")
    first["payload"]["items"] = first["payload"]["items"][:1]
    oid = ingest(pg_engine, scope, tmp_path, first)
    second = scenario("03-seller-reserves-3060.json")
    second["run_id"] = str(uuid4())
    second["capture_id"] = "synthetic-review-second"
    second["payload"]["observed_at"] = "2026-11-07T10:30:00Z"
    second["payload"]["items"] = second["payload"]["items"][:1]
    second["payload"]["items"][0]["source_status_label"] = "ขายแล้ว"
    ingest(pg_engine, scope, tmp_path, second)
    with pg_engine.connect() as conn:
        assert (
            conn.execute(
                select(func.count())
                .select_from(status_facts)
                .where(status_facts.c.validation_result == "confirmed")
            ).scalar_one()
            == 2
        )
        assert conn.execute(select(current_items.c.needs_review)).scalar_one() is True
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
    assert api_post(pg_engine, scope, post_id)["current_items"][0]["needs_review"] is True


def test_effective_time_orders_before_observation_time(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-effective-sort")
    opened = scenario("01-open-both.json")
    ingest(pg_engine, scope, tmp_path, opened)
    reserved = scenario("03-seller-reserves-3060.json")
    reserved["payload"]["comments"][1]["source_time"]["value"] = "2026-11-07T08:30:00Z"
    ingest(pg_engine, scope, tmp_path, reserved)
    with pg_engine.connect() as conn:
        item_id = conn.execute(
            select(listing_items.c.id).where(listing_items.c.source_item_key == "sku3060")
        ).scalar_one()
        assert conn.execute(
            select(sale_events.c.sale_status)
            .where(sale_events.c.item_id == item_id)
            .order_by(sale_events.c.seq)
        ).scalars().all() == ["reserved", "open"]


def test_api_status_since_evidence_and_null_ask(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-api-status")
    document = scenario("03-seller-reserves-3060.json")
    document["payload"]["items"][0]["price"] = None
    oid = ingest(pg_engine, scope, tmp_path, document)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
        evidence_id = conn.execute(
            select(evidence.c.id).where(
                evidence.c.observation_id == oid, evidence.c.local_key == "s03-02-3060"
            )
        ).scalar_one()
    item = api_post(pg_engine, scope, post_id)["current_items"][0]
    assert item["sale_status"] == "reserved"
    assert item["status_since"].replace("+00:00", "Z") == "2026-11-07T09:20:00Z"
    assert item["status_evidence_id"] == str(evidence_id)
    assert item["ask"] is None


def test_rebuild_preserves_cause_keys(pg_engine, tmp_path):
    from paiya.status.service import _rebuild_sale_events_and_projection

    scope = seed_fixture_scope(pg_engine, "synthetic-rebuild")
    directory = SCENARIOS / "S02-late-arrival" / "rounds"
    for filename in ("01-open.json", "03-open.json", "02-reserved.json"):
        ingest(
            pg_engine,
            scope,
            tmp_path,
            json.loads((directory / filename).read_text(encoding="utf-8")),
        )
    with pg_engine.begin() as conn:
        item_id, post_id = conn.execute(
            select(current_items.c.item_id, current_items.c.post_id)
        ).one()
        before = conn.execute(
            select(sale_events.c.seq, sale_events.c.cause_key).order_by(sale_events.c.seq)
        ).all()
        _rebuild_sale_events_and_projection(conn, item_id, post_id)
        assert (
            conn.execute(
                select(sale_events.c.seq, sale_events.c.cause_key).order_by(sale_events.c.seq)
            ).all()
            == before
        )
