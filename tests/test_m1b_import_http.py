"""M1b HTTP acceptance against isolated PostgreSQL 17 schemas."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from test_m1_manual_import import (  # noqa: F401
    PERMALINK,
    TWO_ITEM_TEXT,
    _install_network_spies,
)

from paiya.api import app as app_module
from paiya.api import auth as auth_api
from paiya.api import imports as imports_api
from paiya.api.app import app
from paiya.manual.service import ManualImportService
from paiya.store.tables import observations, posts, source_grants

pytest_plugins = ("test_m1_manual_import",)


@pytest.fixture
def client(pg_engine, monkeypatch):
    monkeypatch.setattr(app_module, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(auth_api, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(imports_api, "get_database_engine", lambda: pg_engine)
    app.dependency_overrides.clear()
    with TestClient(app) as instance:
        yield instance
    app.dependency_overrides.clear()


@pytest.fixture
def group_id(pg_engine, manual_grant):
    with pg_engine.connect() as conn:
        return str(
            conn.execute(
                select(source_grants.c.group_id).where(source_grants.c.id == manual_grant)
            ).scalar_one()
        )


def headers(session, key=None):
    result = {"Origin": "http://127.0.0.1:8137", "X-CSRF-Token": session["csrf_token"]}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def body():
    return {"permalink": PERMALINK, "text": TWO_ITEM_TEXT}


def preview(client, group_id, session):
    return client.post(
        f"/v1/imports/preview?group_id={group_id}", json=body(), headers=headers(session)
    )


def commit(client, group_id, session, digest, key="synthetic-key", payload=None):
    return client.post(
        f"/v1/imports?group_id={group_id}",
        json={**(body() if payload is None else payload), "preview_digest": digest},
        headers=headers(session, key),
    )


def count(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def test_at11_http_idempotency_and_no_external_network(
    pg_engine, client, group_id, authenticated_session, monkeypatch
):
    attempts = _install_network_spies(monkeypatch)
    first_preview = preview(client, group_id, authenticated_session)
    assert first_preview.status_code == 200
    assert first_preview.headers["cache-control"] == "no-store"
    assert count(pg_engine, observations) == 0
    digest = first_preview.json()["preview_digest"]
    first = commit(client, group_id, authenticated_session, digest)
    retry = commit(client, group_id, authenticated_session, digest)
    assert (first.status_code, retry.status_code) == (201, 200)
    assert first.json() == retry.json()
    assert first.headers["cache-control"] == retry.headers["cache-control"] == "no-store"
    assert count(pg_engine, posts) == 1
    assert count(pg_engine, observations) == 1
    again = commit(client, group_id, authenticated_session, digest, "new-intent")
    assert again.status_code == 201
    assert again.json()["post_id"] == first.json()["post_id"]
    assert again.json()["observation_id"] != first.json()["observation_id"]
    assert count(pg_engine, posts) == 1
    assert count(pg_engine, observations) == 2
    assert attempts == []


def test_conflict_digest_and_validation(pg_engine, client, group_id, authenticated_session):
    p = preview(client, group_id, authenticated_session)
    digest = p.json()["preview_digest"]
    assert commit(client, group_id, authenticated_session, digest).status_code == 201
    changed = {**body(), "text": TWO_ITEM_TEXT + "\nsynthetic extra"}
    conflict = commit(client, group_id, authenticated_session, digest, payload=changed)
    assert (conflict.status_code, conflict.json()["detail"]["code"]) == (
        409,
        "idempotency_conflict",
    )
    malformed_changed = {**body(), "permalink": "https://example.invalid/"}
    malformed_conflict = commit(
        client,
        group_id,
        authenticated_session,
        digest,
        payload=malformed_changed,
    )
    assert (malformed_conflict.status_code, malformed_conflict.json()["detail"]["code"]) == (
        409,
        "idempotency_conflict",
    )
    mismatch = commit(client, group_id, authenticated_session, "wrong-digest", "another-key")
    assert (mismatch.status_code, mismatch.json()["detail"]["code"]) == (
        409,
        "preview_digest_mismatch",
    )
    assert count(pg_engine, observations) == 1
    for field, value in [("permalink", "https://example.invalid/"), ("text", "")]:
        bad = client.post(
            f"/v1/imports/preview?group_id={group_id}",
            json={**body(), field: value},
            headers=headers(authenticated_session),
        )
        assert bad.status_code == 422
        assert bad.json()["detail"]["field"] == field
        assert TWO_ITEM_TEXT not in bad.text
    for key in (None, " ", "x" * 201):
        bad = commit(client, group_id, authenticated_session, digest, key)
        assert bad.status_code == 422
        assert bad.json()["detail"]["field"] == "Idempotency-Key"
    large = client.post(
        f"/v1/imports/preview?group_id={group_id}",
        json={**body(), "text": "x" * (1024 * 1024 + 1)},
        headers=headers(authenticated_session),
    )
    assert large.status_code == 413
    oversized = client.post(
        f"/v1/imports/preview?group_id={group_id}",
        content=b"x" * (imports_api.MAX_IMPORT_BODY_BYTES + 1),
        headers={**headers(authenticated_session), "Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    unsupported = client.post(
        f"/v1/imports/preview?group_id={group_id}",
        content=b"{}",
        headers={**headers(authenticated_session), "Content-Type": "text/plain"},
    )
    assert unsupported.status_code == 415


def test_session_csrf_and_origin(client, group_id, authenticated_session):
    preview_url = f"/v1/imports/preview?group_id={group_id}"
    commit_url = f"/v1/imports?group_id={group_id}"
    for url in (preview_url, commit_url):
        assert (
            client.post(
                url,
                json=body(),
                headers={"Origin": "http://127.0.0.1:8137"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                url,
                json={**body(), "preview_digest": "synthetic-digest"},
                headers={**headers(authenticated_session), "Origin": "https://example.invalid"},
            ).status_code
            == 403
        )

    client.cookies.clear()
    for url in (preview_url, commit_url):
        assert (
            client.post(
                url,
                json={**body(), "preview_digest": "synthetic-digest"},
                headers=headers(authenticated_session),
            ).status_code
            == 401
        )


def test_grant_absence_and_revocation(
    pg_engine, client, group_id, manual_grant, authenticated_session
):
    p = preview(client, group_id, authenticated_session)
    assert p.status_code == 200
    with pg_engine.begin() as conn:
        conn.execute(
            update(source_grants)
            .where(source_grants.c.id == manual_grant)
            .values(revoked_at=datetime.now(UTC))
        )
    missing = commit(client, group_id, authenticated_session, p.json()["preview_digest"])
    unknown = commit(client, str(uuid4()), authenticated_session, p.json()["preview_digest"])
    assert missing.status_code == unknown.status_code == 404
    assert missing.json() == unknown.json()
    assert count(pg_engine, posts) == count(pg_engine, observations) == 0
    # Even an oversized body is not read before access is resolved.
    absent_preview = client.post(
        f"/v1/imports/preview?group_id={group_id}",
        content=b"x" * (imports_api.MAX_IMPORT_BODY_BYTES + 1),
        headers={**headers(authenticated_session), "Content-Type": "application/json"},
    )
    assert absent_preview.status_code == 404
    assert absent_preview.json() == unknown.json()


def test_grant_rechecked_inside_commit_transaction(
    pg_engine, client, group_id, manual_grant, authenticated_session, monkeypatch
):
    p = preview(client, group_id, authenticated_session)
    original = ManualImportService.commit_with_status

    def revoke_before_transaction(self, *args, **kwargs):
        with pg_engine.begin() as conn:
            conn.execute(
                update(source_grants)
                .where(source_grants.c.id == manual_grant)
                .values(revoked_at=datetime.now(UTC))
            )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ManualImportService, "commit_with_status", revoke_before_transaction)
    result = commit(client, group_id, authenticated_session, p.json()["preview_digest"])
    assert result.status_code == 404
    assert count(pg_engine, posts) == count(pg_engine, observations) == 0


def test_revoked_grant_never_attempts_persistence(
    pg_engine, client, group_id, manual_grant, authenticated_session, monkeypatch
):
    import paiya.manual.service as manual_service

    p = preview(client, group_id, authenticated_session)
    calls = []
    original_commit = ManualImportService.commit_with_status
    original_persist = manual_service._persist_observation

    def track_persist(*args, **kwargs):
        calls.append(True)
        return original_persist(*args, **kwargs)

    def revoke_before_commit(self, *args, **kwargs):
        with pg_engine.begin() as conn:
            conn.execute(
                update(source_grants)
                .where(source_grants.c.id == manual_grant)
                .values(revoked_at=datetime.now(UTC))
            )
        return original_commit(self, *args, **kwargs)

    monkeypatch.setattr(manual_service, "_persist_observation", track_persist)
    monkeypatch.setattr(ManualImportService, "commit_with_status", revoke_before_commit)
    result = commit(client, group_id, authenticated_session, p.json()["preview_digest"])
    assert result.status_code == 404
    assert calls == []


def test_non_manual_grant_is_not_selected_for_import(
    pg_engine, client, group_id, manual_grant, authenticated_session, monkeypatch
):
    import paiya.manual.service as manual_service

    with pg_engine.begin() as conn:
        conn.execute(
            update(source_grants)
            .where(source_grants.c.id == manual_grant)
            .values(source_kind="facebook_live")
        )
    loaded = []
    original = manual_service._load_grant

    def track_load(*args, **kwargs):
        loaded.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(manual_service, "_load_grant", track_load)
    responses = (
        preview(client, group_id, authenticated_session),
        commit(client, group_id, authenticated_session, "synthetic-digest"),
    )
    for response in responses:
        assert (response.status_code, response.json()["detail"]["code"]) == (
            404,
            "import_grant_unavailable",
        )
    assert loaded == []
    assert count(pg_engine, posts) == count(pg_engine, observations) == 0


def test_retry_with_wrong_digest_is_rejected(pg_engine, client, group_id, authenticated_session):
    digest = preview(client, group_id, authenticated_session).json()["preview_digest"]
    assert commit(client, group_id, authenticated_session, digest).status_code == 201
    retry = commit(client, group_id, authenticated_session, "wrong-digest")
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "preview_digest_mismatch"
    assert count(pg_engine, posts) == count(pg_engine, observations) == 1


def test_streaming_body_limit_without_content_length(
    client, group_id, authenticated_session, monkeypatch
):
    monkeypatch.setattr(imports_api, "MAX_IMPORT_BODY_BYTES", 32)
    request = client.build_request(
        "POST",
        f"/v1/imports/preview?group_id={group_id}",
        content=iter([b"x" * 20, b"x" * 20]),
        headers={**headers(authenticated_session), "Content-Type": "application/json"},
    )
    assert "content-length" not in request.headers
    response = client.send(request)
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "payload_too_large"
