"""A5a synthetic read API and demo contract checks on PostgreSQL 17."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a4c_seller import envelope, record

from paiya.api import evidence as api
from paiya.api.app import app
from paiya.demo import seed_demo
from paiya.read.service import read_posts
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.tables import evidence, metadata, observations, posts, source_grants

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(pg_engine, monkeypatch):
    monkeypatch.setattr(api, "get_database_engine", lambda: pg_engine)
    monkeypatch.delenv("PAIYA_READ_CONTEXT_GROUP_KEY", raising=False)
    app.dependency_overrides.clear()
    with TestClient(app) as instance:
        yield instance
    app.dependency_overrides.clear()


def test_context_endpoints_and_untrusted_group(pg_engine, client, monkeypatch):
    scope = seed_fixture_scope(pg_engine, "synthetic-demo")
    other = seed_fixture_scope(pg_engine, "synthetic-other")
    post, seller = record(pg_engine, scope, Path.cwd(), envelope(scope, "context"))
    record(pg_engine, other, Path.cwd(), envelope(other, "other"))
    missing = uuid4()
    paths = [
        "/v1/posts",
        "/v1/groups",
        "/v1/watches",
        f"/v1/posts/{post}",
        f"/v1/posts/{post}/timeline",
        f"/v1/sellers/{seller}",
        f"/v1/evidence/{missing}",
        f"/v1/evidence/{missing}/media",
        f"/v1/groups/{scope.group_id}/runs",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "read_context_required"
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-demo")
    for path in paths[:6] + paths[8:]:
        assert client.get(path).status_code == 200
    response = client.get(
        "/v1/groups",
        params={"group_key": other.group_key},
        headers={"X-Group-Key": other.group_key},
    )
    assert [g["group_key"] for g in response.json()["groups"]] == [scope.group_key]
    for field, value in [
        ("revoked_at", datetime.now(UTC)),
        ("valid_until", datetime.now(UTC) - timedelta(seconds=1)),
    ]:
        with pg_engine.begin() as conn:
            conn.execute(
                update(source_grants)
                .where(source_grants.c.id == scope.grant_id)
                .values(revoked_at=None, valid_until=None)
            )
            conn.execute(
                update(source_grants)
                .where(source_grants.c.id == scope.grant_id)
                .values(**{field: value})
            )
        response = client.get("/v1/posts")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "grant_unavailable"


def test_invalid_context_rejected_at_server_and_dependency(monkeypatch, capsys):
    from paiya import serve

    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "real-group")
    with patch.object(serve.uvicorn, "run") as run, pytest.raises(SystemExit) as error:
        serve.main()
    assert error.value.code == 2
    assert "PAIYA_READ_CONTEXT_GROUP_KEY must start with 'synthetic-'" in capsys.readouterr().err
    run.assert_not_called()
    with pytest.raises(ValueError, match="must start with"):
        api.require_read_context()
    with pytest.raises(ValueError, match="must start with"), TestClient(app):
        pass


def test_demo_cards_groups_media_and_idempotence(pg_engine, client, monkeypatch, tmp_path):
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    result = seed_demo(pg_engine)
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-demo")
    with pg_engine.connect() as conn:
        tables = metadata.sorted_tables
        counts = [conn.execute(select(func.count()).select_from(t)).scalar_one() for t in tables]
    assert seed_demo(pg_engine)["observation_ids"] == result["observation_ids"]
    with pg_engine.connect() as conn:
        assert [
            conn.execute(select(func.count()).select_from(t)).scalar_one() for t in tables
        ] == counts
        captured = (
            conn.execute(select(evidence.c.id).where(evidence.c.media_state == "captured"))
            .scalars()
            .all()
        )
        failed = (
            conn.execute(
                select(evidence.c.media_reason).where(evidence.c.media_state == "capture_failed")
            )
            .scalars()
            .all()
        )
        assert failed and all(failed)
    listing = client.get("/v1/posts").json()
    assert set(listing) == {"posts", "next_cursor"}
    cards = listing["posts"]
    assert len(cards) >= 3
    assert all(
        set(c)
        == {
            "post_id",
            "group_id",
            "permalink",
            "posted_at",
            "last_success_at",
            "has_unresolved_items",
            "watched",
            "thumbnail_evidence_id",
            "items",
        }
        for c in cards
    )
    assert all(c["permalink"].startswith("https://example.invalid/") for c in cards)
    assert all(
        set(i)
        == {
            "item_id",
            "source_item_key",
            "model",
            "model_key",
            "vram_gb",
            "condition",
            "warranty",
            "warranty_until",
            "warranty_months_left",
            "sale_status",
            "status_since",
            "needs_review",
            "ask",
        }
        and i["source_item_key"]
        for c in cards
        for i in c["items"]
    )
    assert any(
        len(c["items"]) > 1 and any(i["sale_status"] == "reserved" for i in c["items"])
        for c in cards
    )
    assert any(c["thumbnail_evidence_id"] is None for c in cards)
    for image_id in captured:
        assert client.get(f"/v1/evidence/{image_id}").status_code == 200
        response = client.get(f"/v1/evidence/{image_id}/media")
        assert response.status_code == 200
        assert response.content in [
            p.read_bytes() for p in (ROOT / "fixtures/demo/images").glob("*.webp")
        ]
        assert response.headers["cache-control"] == "no-store"
    rows = client.get("/v1/watches").json()["watches"]
    assert len(rows) == 1
    assert rows[0]["post"] == next(c for c in cards if c["post_id"] == rows[0]["post_id"])
    group = client.get("/v1/groups").json()["groups"][0]
    assert set(group) == {
        "group_id",
        "group_key",
        "enabled",
        "poll_minutes",
        "last_success_at",
        "last_run",
    }
    runs = client.get(f"/v1/groups/{group['group_id']}/runs").json()["runs"]
    assert group["last_run"] == (runs[0] if runs else None)
    with pg_engine.connect() as conn:
        seller_ids = conn.execute(
            select(posts.c.seller_id, func.count()).group_by(posts.c.seller_id)
        ).all()
    seller = next(s for s, count in seller_ids if s and count >= 2)
    assert client.get(f"/v1/sellers/{seller}").json()["display_name"]
    for path in ["/v1/posts", "/v1/groups", "/v1/unknown", "/v1/posts?limit=51"]:
        assert client.get(path).headers["cache-control"] == "no-store"


def test_demo_without_media_root_creates_nothing(pg_engine, monkeypatch):
    monkeypatch.delenv("PAIYA_FIXTURE_MEDIA_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="PAIYA_FIXTURE_MEDIA_ROOT is required"):
        seed_demo(pg_engine)
    with pg_engine.connect() as conn:
        for table in metadata.sorted_tables:
            assert conn.execute(select(func.count()).select_from(table)).scalar_one() == 0


def test_posts_cursor_and_filters(pg_engine, client, monkeypatch, tmp_path):
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    seed_demo(pg_engine)
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-demo")
    all_cards = client.get("/v1/posts").json()["posts"]
    expected = sorted(all_cards, key=lambda c: c["post_id"])
    expected.sort(key=lambda c: c["last_success_at"], reverse=True)
    assert all_cards == expected
    assert client.get("/v1/posts?limit=51").status_code == 422
    found = []
    cursor = None
    while True:
        data = client.get(
            "/v1/posts", params={"limit": 1, **({"cursor": cursor} if cursor else {})}
        ).json()
        found.extend(data["posts"])
        cursor = data["next_cursor"]
        if cursor is None:
            break
    assert found == all_cards
    reserved = client.get("/v1/posts?sale_status=reserved").json()["posts"]
    assert reserved == [
        c for c in all_cards if any(i["sale_status"] == "reserved" for i in c["items"])
    ]
    assert reserved
    priced = client.get("/v1/posts?min_price=3200&max_price=3200").json()["posts"]
    assert priced and all(
        any(
            i["ask"] and i["ask"]["currency"] == "THB" and float(i["ask"]["amount"]) == 3200
            for i in c["items"]
        )
        for c in priced
    )


def test_posts_scope_excludes_second_grant(pg_engine, tmp_path):
    a = seed_fixture_scope(pg_engine, "synthetic-grants")
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
    record(pg_engine, a, tmp_path, envelope(a, "shared"))
    record(pg_engine, b, tmp_path, envelope(b, "private", status="sold", minute=10))
    with pg_engine.connect() as conn:
        first = read_posts(conn, a)["posts"]
        second = read_posts(conn, b)["posts"]
    assert len(first) == len(second) == 1
    assert first[0]["post_id"] != second[0]["post_id"]
    assert first[0]["items"][0]["sale_status"] == "open"
    assert second[0]["items"][0]["sale_status"] == "sold"


def test_serve_spa_and_loopback(client, monkeypatch, tmp_path):
    from paiya import serve

    (tmp_path / "index.html").write_text("synthetic app", encoding="utf-8")
    monkeypatch.setenv("PAIYA_WEB_DIST", str(tmp_path))
    assert client.get("/").text == "synthetic app"
    assert client.get("/unknown").text == "synthetic app"
    response = client.get("/v1/unknown")
    assert response.status_code == 404 and response.headers["content-type"].startswith(
        "application/json"
    )
    monkeypatch.setenv("PAIYA_WEB_DIST", str(tmp_path / "missing"))
    assert client.get("/").status_code == 404
    assert client.get("/v1/posts").status_code == 401
    with patch.object(serve.uvicorn, "run") as run:
        serve.main()
    assert run.call_args.kwargs["host"] == "127.0.0.1"


def test_default_limit_and_tied_order(pg_engine, client, monkeypatch, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-many")
    for index in range(21):
        record(pg_engine, scope, tmp_path, envelope(scope, f"post-{index}"))
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", scope.group_key)
    first = client.get("/v1/posts").json()
    assert len(first["posts"]) == 20 and first["next_cursor"]
    second = client.get("/v1/posts", params={"cursor": first["next_cursor"]}).json()
    ids = [p["post_id"] for p in first["posts"] + second["posts"]]
    assert len(ids) == len(set(ids)) == 21
    assert ids == sorted(ids)


def test_seller_latest_name_and_null(pg_engine, client, monkeypatch, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-names")
    old = envelope(scope, "first", actor="alpha")
    old["payload"]["seller"]["display_name"] = "Old"
    _, seller = record(pg_engine, scope, tmp_path, old)
    new = envelope(scope, "second", actor="alpha", minute=10)
    new["payload"]["seller"]["display_name"] = "New"
    record(pg_engine, scope, tmp_path, new)
    absent = envelope(scope, "third", actor="beta")
    absent["payload"]["seller"]["display_name"] = None
    _, unnamed = record(pg_engine, scope, tmp_path, absent)
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", scope.group_key)
    assert client.get(f"/v1/sellers/{seller}").json()["display_name"] == "New"
    assert client.get(f"/v1/sellers/{unnamed}").json()["display_name"] is None


def test_thumbnail_is_first_captured_on_latest_observation(
    pg_engine, client, monkeypatch, tmp_path
):
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    seed_demo(pg_engine)
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-demo")
    cards = client.get("/v1/posts").json()["posts"]
    with pg_engine.connect() as conn:
        for card in cards:
            oid, normalized = conn.execute(
                select(observations.c.id, observations.c.normalized_json)
                .where(observations.c.post_id == card["post_id"])
                .order_by(observations.c.observed_at.desc(), observations.c.id.desc())
                .limit(1)
            ).one()
            captured_keys = [
                item["local_key"]
                for item in normalized["payload"]["evidence"]
                if item["media_state"] == "captured"
            ]
            expected = None
            if captured_keys:
                expected = str(
                    conn.execute(
                        select(evidence.c.id).where(
                            evidence.c.observation_id == oid,
                            evidence.c.local_key == captured_keys[0],
                        )
                    ).scalar_one()
                )
            assert card["thumbnail_evidence_id"] == expected


def test_shared_post_cannot_expose_other_grants_status_or_price(pg_engine, tmp_path):
    from paiya.read.service import read_post

    a = seed_fixture_scope(pg_engine, "synthetic-shared")
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
    first = envelope(a, "shared")
    first["payload"]["items"][0]["price"] = {
        "kind": "ask",
        "unit": "each",
        "amount": "100",
        "currency": "THB",
    }
    post_id, _ = record(pg_engine, a, tmp_path, first)
    with pg_engine.connect() as conn:
        assert read_posts(conn, a)["posts"][0]["items"][0]["ask"]["amount"] == "100.00"
    second = envelope(b, "shared", status="sold", minute=10)
    second["payload"]["items"][0]["price"] = {
        "kind": "ask",
        "unit": "each",
        "amount": "999",
        "currency": "THB",
    }
    record(pg_engine, b, tmp_path, second)
    with pg_engine.connect() as conn:
        for context in (a, b):
            card = read_posts(conn, context)["posts"][0]
            detail = read_post(conn, context, post_id)
            assert [
                (i["item_id"], i["sale_status"], i["ask"]["amount"] if i["ask"] else None)
                for i in card["items"]
            ] == [
                (i["item_id"], i["sale_status"], i["ask"]["amount"] if i["ask"] else None)
                for i in detail["current_items"]
            ]
            if context == a:
                assert all(
                    i["sale_status"] != "sold"
                    and (i["ask"] is None or i["ask"]["amount"] != "999.00")
                    for i in card["items"]
                )
        assert read_posts(conn, b)["posts"][0]["items"][0]["sale_status"] == "sold"
        assert read_posts(conn, b)["posts"][0]["items"][0]["ask"]["amount"] == "999.00"


def test_keyless_item_is_not_in_card(pg_engine, client, monkeypatch, tmp_path):
    scope = seed_fixture_scope(pg_engine, "synthetic-keyless-card")
    document = envelope(scope, "mixed")
    document["payload"]["items"].append(dict(document["payload"]["items"][0], source_item_key=None))
    record(pg_engine, scope, tmp_path, document)
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", scope.group_key)
    card = client.get("/v1/posts").json()["posts"][0]
    assert card["has_unresolved_items"] is True
    assert [item["source_item_key"] for item in card["items"]] == ["item-1"]


def test_price_filter_excludes_non_thb(pg_engine, client, monkeypatch, tmp_path):
    from paiya.store.tables import price_facts

    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    seed_demo(pg_engine)
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-demo")
    at_bound = client.get("/v1/posts?min_price=3200&max_price=3200").json()["posts"]
    assert len(at_bound) == 1
    post_id = at_bound[0]["post_id"]
    assert post_id not in {
        c["post_id"] for c in client.get("/v1/posts?max_price=3199").json()["posts"]
    }
    assert post_id not in {
        c["post_id"] for c in client.get("/v1/posts?min_price=3201").json()["posts"]
    }
    with pg_engine.begin() as conn:
        conn.execute(
            update(price_facts).where(price_facts.c.post_id == post_id).values(currency="USD")
        )
    card = next(c for c in client.get("/v1/posts").json()["posts"] if c["post_id"] == post_id)
    assert card["items"][0]["ask"]["currency"] == "USD"
    assert post_id not in {
        c["post_id"] for c in client.get("/v1/posts?min_price=3200&max_price=3200").json()["posts"]
    }
