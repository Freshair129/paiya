"""A6d model options use the same grant-scoped projections as post cards."""

from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert, update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a6a_extraction import capture

from paiya.api import evidence as api
from paiya.api.app import app
from paiya.api.evidence import get_db_connection, require_fixture_scope
from paiya.ingest.service import ingest_fixture
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.tables import source_grants


def test_models_counts_and_latest_foreign_grant(pg_engine, tmp_path):
    a = seed_fixture_scope(pg_engine, "synthetic-models")
    b = FixtureScope(a.group_id, uuid4(), a.group_key)
    with pg_engine.begin() as conn:
        conn.execute(
            insert(source_grants).values(
                id=b.grant_id,
                group_id=b.group_id,
                source_kind="fixture",
                scopes=["read", "capture", "media", "comments"],
                authority_ref="synthetic-models-b",
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                retention_days=30,
            )
        )
    doc = capture(a, "3060 12GB", "", "3060 12GB", post="multiple")
    doc["payload"]["items"].append(
        dict(doc["payload"]["items"][0], source_item_key="item-2", evidence_keys=["evidence-2"])
    )
    doc["payload"]["evidence"].append(
        {
            "local_key": "evidence-2",
            "role": "seller",
            "source_span": {"field": "post_text", "quote": "3060 12GB"},
            "media_state": "not_requested",
        }
    )
    ingest_fixture(pg_engine, a, doc, {}, tmp_path)
    for post, model in [("other", "1660 super"), ("missing", "unrelated"), ("foreign", "3060")]:
        ingest_fixture(pg_engine, a, capture(a, model, "", model, post=post), {}, tmp_path)
    ingest_fixture(
        pg_engine,
        b,
        capture(b, "3070", "", "3070", post="foreign", observed="2026-11-16T12:00:00Z"),
        {},
        tmp_path,
    )
    ingest_fixture(
        pg_engine,
        a,
        capture(a, "unrelated", "", "unrelated", post="foreign", observed="2026-11-17T12:00:00Z"),
        {},
        tmp_path,
    )

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    try:
        with TestClient(app) as client:
            for scope, expected in [
                (
                    a,
                    [
                        {"model_key": "gtx-1660-super", "item_count": 1},
                        {"model_key": "rtx-3060", "item_count": 2},
                    ],
                ),
                (b, []),
            ]:
                app.dependency_overrides[require_fixture_scope] = lambda scope=scope: scope
                response = client.get("/v1/models")
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
                assert response.json() == {"models": expected}
                cards = client.get("/v1/posts").json()["posts"]
                counts = Counter(
                    item["model_key"]
                    for post in cards
                    for item in post["items"]
                    if item["model_key"] is not None
                )
                assert {row["model_key"]: row["item_count"] for row in expected} == counts
    finally:
        app.dependency_overrides.clear()


def test_models_requires_active_context(pg_engine, monkeypatch):
    scope = seed_fixture_scope(pg_engine, "synthetic-models-auth")
    monkeypatch.setattr(api, "get_database_engine", lambda: pg_engine)
    monkeypatch.delenv("PAIYA_READ_CONTEXT_GROUP_KEY", raising=False)
    app.dependency_overrides.clear()
    try:
        with TestClient(app) as client:
            assert client.get("/v1/models").status_code == 401
            monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", scope.group_key)
            with pg_engine.begin() as conn:
                conn.execute(
                    update(source_grants)
                    .where(source_grants.c.id == scope.grant_id)
                    .values(revoked_at=datetime.now(UTC))
                )
            assert client.get("/v1/models").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_models_match_posts_in_single_grant(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-models-single")
    for name, model in [("one", "3060 12GB"), ("two", "3060 12GB"), ("empty", "unknown")]:
        ingest_fixture(pg_engine, scope, capture(scope, model, "", model, post=name), {}, tmp_path)

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    app.dependency_overrides[require_fixture_scope] = lambda: scope
    try:
        with TestClient(app) as client:
            models = client.get("/v1/models").json()["models"]
            posts = client.get("/v1/posts").json()["posts"]
            counts = Counter(
                item["model_key"]
                for post in posts
                for item in post["items"]
                if item["model_key"] is not None
            )
            assert models == [{"model_key": "rtx-3060", "item_count": 2}]
            assert {row["model_key"]: row["item_count"] for row in models} == counts
    finally:
        app.dependency_overrides.clear()
