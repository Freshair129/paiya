"""Synthetic seller-page identity and context acceptance."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401

from paiya.api.app import app
from paiya.api.evidence import get_db_connection, require_fixture_scope
from paiya.ingest.service import ingest_fixture
from paiya.read.service import read_seller
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.tables import listing_items, observations, posts, sale_events, source_grants


def envelope(
    scope, post, *, actor="seller", identity="synthetic-identity", status="open", minute=0
):
    label = {"open": "เปิดขาย", "reserved": "ติดจอง", "sold": "ขายแล้ว"}[status]
    return {
        "run_id": str(uuid4()),
        "capture_id": f"synthetic-{uuid4()}",
        "grant_id": None,
        "source_kind": "fixture",
        "job_kind": "discover",
        "page_check": {"session": "ok", "sort": "verified_latest"},
        "assets": {},
        "payload": {
            "schema_version": 1,
            "group_key": scope.group_key,
            "source_post_key": post,
            "permalink": f"https://example.invalid/{post}",
            "observed_at": f"2026-11-07T09:{minute:02d}:00Z",
            "access": "ok",
            "posted_at": {
                "source_text": "2026-11-07T08:00:00Z",
                "value": "2026-11-07T08:00:00Z",
                "precision": "exact",
            },
            "activity": {
                "source_reported_comment_count": 0,
                "distinct_comments_seen": 0,
                "distinct_known_commenters_seen": 0,
                "coverage": "complete",
            },
            "seller": {
                "identity_scope": identity,
                "external_actor_key": actor,
                "display_name": "Synthetic seller",
            },
            "items": [
                {
                    "source_item_key": "item-1",
                    "model": "Synthetic item",
                    "condition_text": "used",
                    "source_status_label": label,
                    "price": None,
                    "evidence_keys": ["status"],
                }
            ],
            "comments": [],
            "evidence": [
                {
                    "local_key": "status",
                    "role": "seller",
                    "source_span": {"field": "post_text", "quote": label},
                    "media_state": "not_requested",
                }
            ],
        },
    }


def record(engine, scope, tmp_path, document):
    oid = ingest_fixture(engine, scope, document, {}, tmp_path)
    with engine.connect() as conn:
        return conn.execute(
            select(posts.c.id, posts.c.seller_id)
            .join(observations, observations.c.post_id == posts.c.id)
            .where(observations.c.id == oid)
        ).one()


def test_sold_status_requires_observation_from_context_grant(pg_engine, tmp_path):
    a = seed_fixture_scope(pg_engine, "synthetic-seller-grants")
    b = FixtureScope(a.group_id, uuid4(), a.group_key)
    with pg_engine.begin() as conn:
        conn.execute(
            insert(source_grants).values(
                id=b.grant_id,
                group_id=b.group_id,
                source_kind="fixture",
                scopes=["read", "capture", "media", "comments"],
                authority_ref="synthetic-second-grant",
                valid_from=datetime.now(UTC),
                retention_days=30,
            )
        )
    post_id, seller_id = record(pg_engine, a, tmp_path, envelope(a, "shared"))
    assert record(pg_engine, b, tmp_path, envelope(b, "shared", status="sold", minute=10)) == (
        post_id,
        seller_id,
    )
    with pg_engine.connect() as conn:
        first = read_seller(conn, a, seller_id)
        second = read_seller(conn, b, seller_id)
    assert first["sold_post_count"] == 0
    assert first["posts"][0]["has_sold_item"] is False
    assert second["sold_post_count"] == 1
    assert second["posts"][0]["has_sold_item"] is True


def test_distinct_keys_and_scopes_never_merge_sellers(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-seller-identity")
    p1, s1 = record(pg_engine, scope, tmp_path, envelope(scope, "one", actor="a"))
    p2, s2 = record(pg_engine, scope, tmp_path, envelope(scope, "two", actor="b"))
    p3, s3 = record(
        pg_engine, scope, tmp_path, envelope(scope, "three", actor="a", identity="synthetic-other")
    )
    assert len({s1, s2, s3}) == 3
    with pg_engine.connect() as conn:
        for seller, post in [(s1, p1), (s2, p2), (s3, p3)]:
            page = read_seller(conn, scope, seller)
            assert [row["post_id"] for row in page["posts"]] == [post]


def test_seller_api_context_and_errors(pg_engine, tmp_path):
    a = seed_fixture_scope(pg_engine, "synthetic-seller-group-a")
    b = seed_fixture_scope(pg_engine, "synthetic-seller-group-b")
    c = seed_fixture_scope(pg_engine, "synthetic-seller-group-c")
    pa, seller = record(pg_engine, a, tmp_path, envelope(a, "group-a"))
    pb, same_seller = record(pg_engine, b, tmp_path, envelope(b, "group-b"))
    assert seller == same_seller and pa != pb

    def db():
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_db_connection] = db
    try:
        with TestClient(app) as client:
            path = f"/v1/sellers/{seller}"
            assert client.get(path).status_code == 401
            for scope, expected_post in [(a, pa), (b, pb)]:
                app.dependency_overrides[require_fixture_scope] = lambda: scope
                response = client.get(path)
                assert response.status_code == 200
                page = response.json()
                assert page["group_ids"] == [str(scope.group_id)]
                assert [post["post_id"] for post in page["posts"]] == [str(expected_post)]
            app.dependency_overrides[require_fixture_scope] = lambda: c
            for target in (seller, uuid4()):
                response = client.get(f"/v1/sellers/{target}")
                assert response.status_code == 404
                assert response.json()["detail"]["code"] == "not_found"
            app.dependency_overrides[require_fixture_scope] = lambda: a
            with pg_engine.begin() as conn:
                conn.execute(
                    update(source_grants)
                    .where(source_grants.c.id == a.grant_id)
                    .values(revoked_at=datetime.now(UTC))
                )
            assert client.get(path).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_counts_current_sold_posts_and_orders_first_seen(pg_engine, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-seller-counts")
    p1, seller = record(pg_engine, scope, tmp_path, envelope(scope, "reopened", minute=5))
    record(pg_engine, scope, tmp_path, envelope(scope, "reopened", status="reserved", minute=10))
    record(pg_engine, scope, tmp_path, envelope(scope, "reopened", status="open", minute=15))
    sold = envelope(scope, "sold", status="sold", minute=5)
    second_item = dict(
        sold["payload"]["items"][0], source_item_key="item-2", source_status_label="เปิดขาย"
    )
    sold["payload"]["items"].append(second_item)
    p2, _ = record(pg_engine, scope, tmp_path, sold)
    p3, _ = record(pg_engine, scope, tmp_path, envelope(scope, "earliest", minute=2))
    record(pg_engine, scope, tmp_path, envelope(scope, "earliest", minute=20))
    with pg_engine.connect() as conn:
        page = read_seller(conn, scope, seller)
        history = (
            conn.execute(
                select(sale_events.c.sale_status)
                .join(listing_items, listing_items.c.id == sale_events.c.item_id)
                .where(listing_items.c.post_id == p1)
                .order_by(sale_events.c.seq)
            )
            .scalars()
            .all()
        )
    assert history == ["open", "reserved", "open"]
    assert set(page) == {
        "id",
        "identity_scope",
        "display_name",
        "first_seen_at",
        "group_ids",
        "post_count",
        "sold_post_count",
        "posts",
    }
    assert page["first_seen_at"] == datetime(2026, 11, 7, 9, 2, tzinfo=UTC)
    assert page["group_ids"] == [scope.group_id]
    assert page["post_count"] == 3 and page["sold_post_count"] == 1
    assert [post["post_id"] for post in page["posts"]] == [p3, *sorted([p1, p2])]
    assert all(
        set(post) == {"post_id", "permalink", "first_seen_at", "has_sold_item"}
        for post in page["posts"]
    )
    assert {post["post_id"]: post["has_sold_item"] for post in page["posts"]} == {
        p1: False,
        p2: True,
        p3: False,
    }
