"""End-to-end A2 fixture-only SQL, evidence and media acceptance tests."""

import base64
import copy
import hashlib
import json
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, func, select, update
from sqlalchemy.exc import IntegrityError

from paiya.api.app import app
from paiya.api.evidence import get_db_connection, require_fixture_scope
from paiya.ingest.service import CaptureConflict, ingest_fixture
from paiya.store.fixture_scope import FixtureScope, seed_fixture_scope
from paiya.store.media import cleanup_orphans, media_path
from paiya.store.tables import evidence, media_assets, observations, source_grants

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "fixtures/scenarios/S01-reserve-reopen/rounds/01-open.json"
WEBP = base64.b64decode(
    "UklGRi4AAABXRUJQVlA4ICIAAABQAQCdASoCAAIAAkA4JQBOgCgAAP7tBywJK1ZulhhMGnAA"
)


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    url = os.environ.get("PAIYA_TEST_DATABASE_URL")
    if not url:
        pytest.fail("PAIYA_TEST_DATABASE_URL required: A2 tests must run on PostgreSQL 17")
    if not url.startswith("postgresql+psycopg://"):
        pytest.fail("A2 requires PostgreSQL (postgresql+psycopg://), never SQLite")
    engine = create_engine(url)
    with engine.connect() as connection:
        version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    assert version // 10000 == 17, "A2 acceptance requires PostgreSQL 17"
    config = Config(str(ROOT / "alembic.ini"))
    os.environ["PAIYA_DATABASE_URL"] = url
    command.upgrade(config, "head")
    try:
        yield engine
    finally:
        engine.dispose()


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
                select(func.count()).select_from(observations).where(
                    observations.c.capture_id == capture_id
                )
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
            assert image.headers["content-type"] == "image/webp"
            # A separate synthetic group cannot read this known post or image.
            other_scope = seed_fixture_scope(pg_engine, f"synthetic-{uuid4().hex}")
            app.dependency_overrides[require_fixture_scope] = lambda: other_scope
            assert client.get(f"/v1/posts/{post_id}").status_code == 404
            assert client.get(f"/v1/evidence/{evidence_id}").status_code == 404
            assert client.get(f"/v1/evidence/{evidence_id}/media").status_code == 404
            app.dependency_overrides[require_fixture_scope] = lambda: scope
            with pg_engine.begin() as conn:
                conn.execute(
                    update(source_grants)
                    .where(source_grants.c.id == scope.grant_id)
                    .values(revoked_at=datetime.now(timezone.utc))
                )
            assert client.get(f"/v1/evidence/{evidence_id}/media").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_untrusted_api_without_scope_denied() -> None:
    with TestClient(app) as client:
        assert client.get(f"/v1/evidence/{uuid4()}").status_code == 403


def test_idempotency_and_different_capture_id(
    pg_engine: Engine, scope: FixtureScope, document: dict, tmp_path: Path
) -> None:
    first = ingest_fixture(pg_engine, scope, document, {}, tmp_path)
    again = ingest_fixture(pg_engine, scope, copy.deepcopy(document), {}, tmp_path)
    assert first == again
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
            select(func.count()).select_from(observations).where(
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
    monkeypatch.setenv("PAIYA_DATABASE_URL", os.environ["PAIYA_TEST_DATABASE_URL"])
    monkeypatch.setenv("PAIYA_FIXTURE_MEDIA_ROOT", str(tmp_path))
    app.dependency_overrides[require_fixture_scope] = lambda: scope
    try:
        with TestClient(app) as client:
            item = client.get(f"/v1/evidence/{evid}")
            assert item.status_code == 200
            assert item.json()["media_reason"] == "fixture image unavailable"
            assert client.get(f"/v1/evidence/{evid}/media").status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("kind", ["schema", "comment-span", "asset-hash", "traversal", "wrong-group"])
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
            (ROOT / "fixtures/scenarios/S03-multi-item/rounds/03-seller-reserves-3060.json")
            .read_text(encoding="utf-8")
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
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(file_path, (old, old))
    assert cleanup_orphans(tmp_path, {file_path.name}, grace_seconds=0) == 0
    assert file_path.is_file()
    assert cleanup_orphans(tmp_path, set(), grace_seconds=0) == 1
    assert not file_path.exists()
