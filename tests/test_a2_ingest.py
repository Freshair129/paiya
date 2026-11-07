"""End-to-end A2 fixture-only SQL, evidence and media acceptance tests."""

import base64
import copy
import hashlib
import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, func, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from paiya.api.app import app
from paiya.api.evidence import get_db_connection, require_fixture_scope
from paiya.ingest.service import CaptureConflict, ingest_fixture
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.media import cleanup_orphans, media_path
from paiya.store.tables import (
    comment_versions,
    comments,
    evidence,
    listing_items,
    observations,
    source_grants,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "fixtures/scenarios/S01-reserve-reopen/rounds/01-open.json"
WEBP = base64.b64decode("UklGRi4AAABXRUJQVlA4ICIAAABQAQCdASoCAAIAAkA4JQBOgCgAAP7tBywJK1ZulhhMGnAA")


@pytest.fixture
def pg_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql+psycopg://"):
        pytest.fail("PAIYA_TEST_DATABASE_URL must name a PostgreSQL 17 database")
    admin = create_engine(url)
    schema = f"paiya_test_{uuid4().hex}"
    with admin.begin() as conn:
        version = int(conn.exec_driver_sql("SHOW server_version_num").scalar_one())
        assert version // 10000 == 17
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url)
    monkeypatch.setenv("PAIYA_DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def scope(pg_engine: Engine) -> FixtureScope:
    return seed_fixture_scope(pg_engine, f"synthetic-{uuid4().hex}")


@pytest.fixture
def document(scope: FixtureScope) -> dict:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    payload["run_id"] = str(uuid4())
    payload["capture_id"] = f"a2-{uuid4()}"
    payload["payload"]["group_key"] = scope.group_key
    payload["payload"]["source_post_key"] = f"post-{uuid4().hex}"
    return payload


def attach_webp(document: dict) -> dict[str, bytes]:
    meta = {
        "staging_key": "fixture-image-1",
        "sha256": hashlib.sha256(WEBP).hexdigest(),
        "mime": "image/webp",
        "width": 2,
        "height": 2,
        "bytes": len(WEBP),
    }
    document["assets"] = {"s01-open": meta}
    document["payload"]["evidence"][0]["media_state"] = "captured"
    return {"fixture-image-1": WEBP}


def count_observations(engine: Engine, capture_id: str) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count())
                .select_from(observations)
                .where(observations.c.capture_id == capture_id)
            ).scalar_one()
        )


def test_ingest_captured_image_and_read_through_scoped_api(
    pg_engine: Engine,
    scope: FixtureScope,
    document: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets = attach_webp(document)
    oid = ingest_fixture(pg_engine, scope, document, assets, tmp_path)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
        evidence_id = conn.execute(
            select(evidence.c.id).where(evidence.c.observation_id == oid)
        ).scalar_one()
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))

    def test_db() -> Iterator[Connection]:
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[require_fixture_scope] = lambda: scope
    app.dependency_overrides[get_db_connection] = test_db
    try:
        with TestClient(app) as client:
            post = client.get(f"/v1/posts/{post_id}")
            assert post.status_code == 200
            assert post.json()["observations"][0]["id"] == str(oid)
            assert str(evidence_id) in post.json()["observations"][0]["evidence_ids"]
            metadata = client.get(f"/v1/evidence/{evidence_id}")
            assert metadata.status_code == 200
            assert metadata.json()["observation_id"] == str(oid)
            assert metadata.json()["post_id"] == str(post_id)
            image = client.get(f"/v1/evidence/{evidence_id}/media")
            assert image.status_code == 200
            assert image.content == WEBP
            assert image.headers["cache-control"] == "no-store"
            assert image.headers["x-content-type-options"] == "nosniff"
            assert set(post.json()) == {
                "id",
                "group_id",
                "source_post_key",
                "permalink",
                "seller_id",
                "posted_at",
                "observations",
                "current_items",
                "has_unresolved_items",
            }
            observation = post.json()["observations"][0]
            assert set(observation) == {
                "id",
                "run_id",
                "capture_id",
                "observed_at",
                "access",
                "activity",
                "items",
                "evidence_ids",
            }
            assert set(observation["items"][0]) == {
                "item_id",
                "source_item_key",
                "model",
                "condition_text",
                "model_key",
                "vram_gb",
                "condition",
                "warranty",
                "warranty_until",
                "warranty_months_left",
                "source_status_label",
                "price",
                "evidence_ids",
            }
            assert set(metadata.json()) == {
                "id",
                "post_id",
                "observation_id",
                "item_id",
                "local_key",
                "role",
                "source_span",
                "comment_version_id",
                "captured_at",
                "permalink",
                "media_state",
                "media_reason",
                "media",
            }
            assert set(metadata.json()["media"]) == {
                "mime",
                "width",
                "height",
                "bytes",
                "sha256",
                "href",
            }
            assert client.get("/v1/posts/not-a-uuid").status_code == 422
            assert client.get("/v1/evidence/not-a-uuid").status_code == 422
            assert client.get("/v1/evidence/not-a-uuid/media").status_code == 422
            path = media_path(tmp_path, hashlib.sha256(WEBP).hexdigest(), "image/webp")
            for replacement in (b"altered", None):
                if replacement is None:
                    path.unlink()
                else:
                    path.write_bytes(replacement)
                failed = client.get(f"/v1/evidence/{evidence_id}/media")
                assert failed.status_code == 500
                assert failed.json()["detail"]["code"] == "media_integrity_failure"
                assert WEBP not in failed.content
            path.write_bytes(WEBP)
            monkeypatch.delenv("PAIYA_FIXTURE_MEDIA_ROOT")
            unavailable = client.get(f"/v1/evidence/{evidence_id}/media")
            assert unavailable.status_code == 503
            assert unavailable.json()["detail"]["code"] == "media_storage_unavailable"
            monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
            assert image.headers["content-type"] == "image/webp"
            # A separate synthetic group cannot read this known post or image.
            other_scope = seed_fixture_scope(pg_engine, f"synthetic-{uuid4().hex}")
            app.dependency_overrides[require_fixture_scope] = lambda: other_scope
            for path in (
                f"/v1/posts/{post_id}",
                f"/v1/evidence/{evidence_id}",
                f"/v1/evidence/{evidence_id}/media",
            ):
                response = client.get(path)
                assert response.status_code == 404
                assert response.json()["detail"]["code"] == "not_found"
            app.dependency_overrides[require_fixture_scope] = lambda: scope
            with pg_engine.begin() as conn:
                conn.execute(
                    update(source_grants)
                    .where(source_grants.c.id == scope.grant_id)
                    .values(revoked_at=datetime.now(UTC))
                )
            for path in (
                f"/v1/posts/{post_id}",
                f"/v1/evidence/{evidence_id}",
                f"/v1/evidence/{evidence_id}/media",
            ):
                response = client.get(path)
                assert response.status_code == 403
                assert response.json()["detail"]["code"] == "grant_unavailable"
    finally:
        app.dependency_overrides.clear()


def test_untrusted_api_without_scope_denied() -> None:
    with TestClient(app) as client:
        response = client.get(f"/v1/evidence/{uuid4()}")
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "read_context_required"


def test_idempotency_and_different_capture_id(
    pg_engine: Engine, scope: FixtureScope, document: dict, tmp_path: Path
) -> None:
    first = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    again = ingest_fixture(pg_engine, scope, copy.deepcopy(document), {}, tmp_path)
    assert first == again
    with pg_engine.connect() as conn:
        assert conn.execute(
            select(func.count()).select_from(evidence).where(evidence.c.observation_id == first)
        ).scalar_one() == len(document["payload"]["evidence"])
    assert count_observations(pg_engine, document["capture_id"]) == 1
    changed = copy.deepcopy(document)
    changed["payload"]["items"][0]["model"] = "DIFFERENT"
    with pytest.raises(CaptureConflict, match="capture_id"):
        ingest_fixture(pg_engine, scope, changed, {}, tmp_path)
    new_round = copy.deepcopy(document)
    new_round["run_id"] = str(uuid4())
    new_round["capture_id"] = f"a2-{uuid4()}"
    second = ingest_fixture(pg_engine, scope, new_round, {}, tmp_path)
    assert second != first
    with pg_engine.connect() as conn:
        total = conn.execute(
            select(func.count())
            .select_from(observations)
            .where(
                observations.c.post_id
                == select(observations.c.post_id)
                .where(observations.c.id == first)
                .scalar_subquery()
            )
        ).scalar_one()
    assert total == 2


def test_missing_photo_reason_survives_and_media_fails_closed(
    pg_engine: Engine,
    scope: FixtureScope,
    document: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document["payload"]["evidence"][0]["media_state"] = "capture_failed"
    document["payload"]["evidence"][0]["media_reason"] = "fixture image unavailable"
    oid = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    with pg_engine.connect() as conn:
        evid = conn.execute(
            select(evidence.c.id).where(evidence.c.observation_id == oid)
        ).scalar_one()
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    app.dependency_overrides[require_fixture_scope] = lambda: scope
    try:
        with TestClient(app) as client:
            item = client.get(f"/v1/evidence/{evid}")
            assert item.status_code == 200
            assert item.json()["media_reason"] == "fixture image unavailable"
            assert item.json()["media"] is None
            response = client.get(f"/v1/evidence/{evid}/media")
            assert response.status_code == 404
            assert response.json()["detail"]["code"] == "media_not_available"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "kind", ["schema", "comment-span", "asset-hash", "traversal", "wrong-group"]
)
def test_rejects_invalid_fixture_and_scope(
    kind: str,
    pg_engine: Engine,
    scope: FixtureScope,
    document: dict,
    tmp_path: Path,
) -> None:
    assets: dict[str, bytes] = {}
    if kind == "schema":
        document["payload"]["schema_version"] = 2
    elif kind == "comment-span":
        other = json.loads(
            (
                ROOT / "fixtures/scenarios/S03-multi-item/rounds/03-seller-reserves-3060.json"
            ).read_text(encoding="utf-8")
        )
        other["run_id"] = document["run_id"]
        other["capture_id"] = document["capture_id"]
        other["payload"]["group_key"] = scope.group_key
        document = other
        document["payload"]["evidence"][1]["source_span"]["start"] = 10000
    elif kind == "asset-hash":
        assets = attach_webp(document)
        document["assets"]["s01-open"]["sha256"] = "0" * 64
    elif kind == "traversal":
        assets = attach_webp(document)
        document["assets"]["s01-open"]["staging_key"] = "../invalid"
        assets = {"../invalid": WEBP}
    else:
        document["payload"]["group_key"] = "synthetic-unrelated"
    with pytest.raises((ValueError, PermissionError)):
        ingest_fixture(pg_engine, scope, document, assets, tmp_path)
    assert count_observations(pg_engine, document["capture_id"]) == 0


def test_sql_constraint_failure_rolls_back_new_observation(
    pg_engine: Engine, scope: FixtureScope, document: dict, tmp_path: Path
) -> None:
    ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    colliding = copy.deepcopy(document)
    # Same run + post, but a new capture ID must violate run/post uniqueness.
    colliding["capture_id"] = f"a2-{uuid4()}"
    with pytest.raises(IntegrityError):
        ingest_fixture(pg_engine, scope, colliding, {}, tmp_path)
    assert count_observations(pg_engine, colliding["capture_id"]) == 0


def test_cleanup_does_not_delete_live_referenced_media(tmp_path: Path) -> None:
    asset = tmp_path / "assets"
    asset.mkdir(parents=True)
    saved = hashlib.sha256(WEBP).hexdigest()
    file_path = media_path(tmp_path, saved, "image/webp")
    file_path.write_bytes(WEBP)
    old = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    os.utime(file_path, (old, old))
    assert cleanup_orphans(tmp_path, {file_path.name}, grace_seconds=0) == 0
    assert file_path.is_file()
    assert cleanup_orphans(tmp_path, set(), grace_seconds=0) == 1
    assert not file_path.exists()


def test_empty_comments_accepts_reported_counts() -> None:
    from paiya.ingest.validation import validate_observation

    document = json.loads(SAMPLE.read_text(encoding="utf-8"))
    document["payload"]["comments"] = []
    document["payload"]["activity"]["distinct_comments_seen"] = 1
    document["payload"]["activity"]["distinct_known_commenters_seen"] = 0
    validate_observation(document)


FIXTURES = sorted((ROOT / "fixtures/scenarios").glob("*/rounds/*.json")) + sorted(
    (ROOT / "fixtures/contract/observation/valid").glob("*.json")
)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: str(p.relative_to(ROOT)))
def test_all_valid_fixtures_ingest(
    path: Path, pg_engine: Engine, scope: FixtureScope, tmp_path: Path
) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["group_key"] = scope.group_key
    for meta in document["assets"].values():
        meta.update(
            staging_key="synthetic-webp",
            sha256=hashlib.sha256(WEBP).hexdigest(),
            mime="image/webp",
            width=2,
            height=2,
            bytes=len(WEBP),
        )
    oid = ingest_fixture(pg_engine, scope, document, {"synthetic-webp": WEBP}, tmp_path)
    assert count_observations(pg_engine, document["capture_id"]) == 1
    assert oid is not None


def test_buyer_comment_mislabeled_as_seller_is_stored(
    pg_engine: Engine, scope: FixtureScope, tmp_path: Path
) -> None:
    path = ROOT / "fixtures/scenarios/S03-multi-item/rounds/02-buyer-only.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["group_key"] = scope.group_key
    entry = copy.deepcopy(document["payload"]["evidence"][0])
    entry.update(local_key="mislabeled", role="seller")
    document["payload"]["evidence"].append(entry)
    oid = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    with pg_engine.connect() as conn:
        row = (
            conn.execute(
                select(evidence).where(
                    evidence.c.observation_id == oid, evidence.c.local_key == "mislabeled"
                )
            )
            .mappings()
            .one()
        )
        assert row["role"] == "seller"
        assert row["comment_version_id"] is not None
    assert count_observations(pg_engine, document["capture_id"]) == 1


def test_comment_identity_and_parent(
    pg_engine: Engine, scope: FixtureScope, tmp_path: Path
) -> None:
    path = ROOT / "fixtures/scenarios/S03-multi-item/rounds/02-buyer-only.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["group_key"] = scope.group_key
    original = document["payload"]["comments"][0]
    keyless = copy.deepcopy(original)
    keyless["source_comment_key"] = None
    keyless["parent_key"] = original["source_comment_key"]
    document["payload"]["comments"].append(keyless)
    document["payload"]["activity"]["distinct_comments_seen"] = 2
    first = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    second_document = copy.deepcopy(document)
    second_document["run_id"] = str(uuid4())
    second_document["capture_id"] = str(uuid4())
    second = ingest_fixture(pg_engine, scope, second_document, {}, tmp_path)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == first)
        ).scalar_one()
        rows = conn.execute(select(comments).where(comments.c.post_id == post_id)).mappings().all()
        keyed = next(row for row in rows if row["source_comment_key"] is not None)
        keyless_rows = [row for row in rows if row["source_comment_key"] is None]
        assert len(keyless_rows) == 2
        assert all(row["parent_id"] == keyed["id"] for row in keyless_rows)
        versions = (
            conn.execute(
                select(comment_versions).where(comment_versions.c.comment_id == keyed["id"])
            )
            .mappings()
            .all()
        )
        assert {row["observation_id"] for row in versions} == {first, second}


def test_concurrent_replay(
    pg_engine: Engine, scope: FixtureScope, document: dict, tmp_path: Path
) -> None:
    barrier = Barrier(3)

    def run() -> object:
        barrier.wait(timeout=10)
        return ingest_fixture(pg_engine, scope, copy.deepcopy(document), {}, tmp_path)

    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(lambda _: run(), range(3)))
    assert len(set(ids)) == 1
    assert count_observations(pg_engine, document["capture_id"]) == 1
    with pg_engine.connect() as conn:
        assert conn.execute(
            select(func.count()).select_from(evidence).where(evidence.c.observation_id == ids[0])
        ).scalar_one() == len(document["payload"]["evidence"])


def test_grant_revoked_during_capture_rolls_back(
    pg_engine: Engine,
    scope: FixtureScope,
    document: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paiya.ingest.service as service

    assets = attach_webp(document)
    original = service.write_media

    def revoke(*args: object, **kwargs: object) -> object:
        with pg_engine.begin() as conn:
            conn.execute(
                update(source_grants)
                .where(source_grants.c.id == scope.grant_id)
                .values(revoked_at=datetime.now(UTC))
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "write_media", revoke)
    with pytest.raises(PermissionError):
        ingest_fixture(pg_engine, scope, document, assets, tmp_path)
    assert count_observations(pg_engine, document["capture_id"]) == 0


def test_expired_grant_denies_ingest_and_reads(
    pg_engine: Engine, scope: FixtureScope, document: dict, tmp_path: Path
) -> None:
    oid = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    with pg_engine.begin() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == oid)
        ).scalar_one()
        conn.execute(
            update(source_grants)
            .where(source_grants.c.id == scope.grant_id)
            .values(valid_until=datetime.now(UTC) - timedelta(days=1))
        )
    with pytest.raises(PermissionError):
        ingest_fixture(pg_engine, scope, document, {}, tmp_path)

    def db() -> Iterator[Connection]:
        with pg_engine.connect() as conn:
            yield conn

    app.dependency_overrides[require_fixture_scope] = lambda: scope
    app.dependency_overrides[get_db_connection] = db
    try:
        with TestClient(app) as client:
            result = client.get(f"/v1/posts/{post_id}")
            assert result.status_code == 403
            assert result.json()["detail"]["code"] == "grant_unavailable"
    finally:
        app.dependency_overrides.clear()


def test_item_mapping_and_observation_local_evidence(
    pg_engine: Engine, scope: FixtureScope, document: dict, tmp_path: Path
) -> None:
    keyless = copy.deepcopy(document["payload"]["items"][0])
    keyless["source_item_key"] = None
    document["payload"]["items"].append(keyless)
    first = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    second_document = copy.deepcopy(document)
    second_document["capture_id"] = str(uuid4())
    second_document["run_id"] = str(uuid4())
    second = ingest_fixture(pg_engine, scope, second_document, {}, tmp_path)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == first)
        ).scalar_one()
        rows = (
            conn.execute(select(listing_items).where(listing_items.c.post_id == post_id))
            .mappings()
            .all()
        )
        assert len(rows) == 1
        app.dependency_overrides[require_fixture_scope] = lambda: scope
        app.dependency_overrides[get_db_connection] = lambda: conn
        try:
            with TestClient(app) as client:
                response = client.get(f"/v1/posts/{post_id}")
                assert response.status_code == 200
                post = response.json()
        finally:
            app.dependency_overrides.clear()
        for observation in post["observations"]:
            assert observation["id"] in (str(first), str(second))
            keyed, unkeyed = observation["items"]
            assert keyed["item_id"] == str(rows[0]["id"])
            assert unkeyed["item_id"] is None
            expected = {
                row.local_key: str(row.id)
                for row in conn.execute(
                    select(evidence.c.local_key, evidence.c.id).where(
                        evidence.c.observation_id == observation["id"]
                    )
                )
            }
            assert keyed["evidence_ids"] == [
                expected[key] for key in document["payload"]["items"][0]["evidence_keys"]
            ]
            assert unkeyed["evidence_ids"] == keyed["evidence_ids"]


def test_database_constraints(pg_engine: Engine, scope: FixtureScope, tmp_path: Path) -> None:
    path = ROOT / "fixtures/scenarios/S03-multi-item/rounds/02-buyer-only.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["group_key"] = scope.group_key
    first = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    document["run_id"] = str(uuid4())
    document["capture_id"] = str(uuid4())
    second = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    with pg_engine.connect() as conn:
        post_id = conn.execute(
            select(observations.c.post_id).where(observations.c.id == first)
        ).scalar_one()
        version_id = conn.execute(
            select(comment_versions.c.id).where(comment_versions.c.observation_id == first)
        ).scalar_one()
    base = dict(
        id=uuid4(),
        observation_id=second,
        post_id=post_id,
        grant_id=scope.grant_id,
        local_key="invalid",
        role="seller",
        media_state="not_requested",
        captured_at=datetime.now(UTC),
    )
    cases = [
        insert(evidence).values(**{**base, "media_state": "captured"}),
        insert(evidence).values(**{**base, "comment_version_id": version_id}),
        insert(listing_items).values(
            id=uuid4(), post_id=post_id, unit="each", resolution="invalid"
        ),
        update(source_grants).where(source_grants.c.id == scope.grant_id).values(retention_days=0),
        update(source_grants).where(source_grants.c.id == scope.grant_id).values(retention_days=-1),
    ]
    for statement in cases:
        with pytest.raises(IntegrityError), pg_engine.begin() as conn:
            conn.execute(statement)
