"""S1 owner-session acceptance tests on isolated PostgreSQL schemas."""

import getpass
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import update as sql_update
from test_a2_ingest import pg_engine as pg_engine  # noqa: F401
from test_a4c_seller import envelope, record

from paiya.api import app as app_module
from paiya.api import auth as auth_api
from paiya.api import evidence as evidence_api
from paiya.api.app import app
from paiya.read.context import ReadableGrant
from paiya.read.errors import GrantUnavailable
from paiya.read.service import read_posts
from paiya.store.fixture_scope import seed_fixture_scope
from paiya.store.tables import owner_sessions, source_grants


@pytest.fixture
def client(pg_engine, monkeypatch):
    monkeypatch.setenv("PAIYA_PORT", "8137")
    monkeypatch.setattr(app_module, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(evidence_api, "get_database_engine", lambda: pg_engine)
    monkeypatch.setattr(auth_api, "get_database_engine", lambda: pg_engine)
    app.dependency_overrides.clear()
    with TestClient(app) as instance:
        yield instance
    app.dependency_overrides.clear()


def unlock(client, passphrase):
    challenge = client.get("/v1/auth/challenge", headers={"Host": "127.0.0.1:8137"})
    return client.post(
        "/v1/auth/unlock",
        headers={
            "Host": "127.0.0.1:8137",
            "Origin": "http://127.0.0.1:8137",
            "X-CSRF-Token": challenge.json()["nonce"],
        },
        json={"passphrase": passphrase},
    )


def test_group_key_alone_does_not_authorize_reads(pg_engine, client, monkeypatch):
    scope = seed_fixture_scope(pg_engine, f"synthetic-s1-{uuid4().hex}")
    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", scope.group_key)
    monkeypatch.delenv("PAIYA_DEV_FIXTURE_MODE", raising=False)

    response = client.get("/v1/posts", headers={"Host": "127.0.0.1:8137"})

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_required"
    trend_response = client.get(
        "/v1/prices/trends",
        params={"model_key": "Synthetic item"},
        headers={"Host": "127.0.0.1:8137"},
    )
    assert trend_response.status_code == 401


def test_owner_session_reads_only_the_readable_grants(
    pg_engine, client, monkeypatch, authenticated_session, tmp_path
):
    from paiya.read.context import resolve_readable_grants

    allowed_scope = seed_fixture_scope(pg_engine, f"synthetic-s1-allowed-{uuid4().hex}")
    outside_scope = seed_fixture_scope(pg_engine, f"synthetic-s1-outside-{uuid4().hex}")
    allowed_post = record(
        pg_engine, allowed_scope, tmp_path, envelope(allowed_scope, "allowed-post")
    )[0]
    record(pg_engine, outside_scope, tmp_path, envelope(outside_scope, "outside-post"))
    with pg_engine.connect() as connection:
        allowed_grants = [
            grant
            for grant in resolve_readable_grants(connection)
            if grant.grant_id == allowed_scope.grant_id
        ]
    monkeypatch.setattr(
        evidence_api,
        "resolve_readable_grants",
        lambda _connection: allowed_grants,
        raising=False,
    )
    monkeypatch.delenv("PAIYA_DEV_FIXTURE_MODE", raising=False)
    monkeypatch.delenv("PAIYA_READ_CONTEXT_GROUP_KEY", raising=False)
    assert authenticated_session["session_id"]

    response = client.get("/v1/posts", headers={"Host": "127.0.0.1:8137"})

    assert response.status_code == 200
    assert {UUID(post["post_id"]) for post in response.json()["posts"]} == {allowed_post}
    trend_response = client.get(
        "/v1/prices/trends",
        params={"model_key": "Synthetic item"},
        headers={"Host": "127.0.0.1:8137"},
    )
    assert trend_response.status_code == 200


def test_price_trend_rejects_multiple_readable_grants(
    pg_engine, client, monkeypatch, authenticated_session
):
    seed_fixture_scope(pg_engine, f"synthetic-s1-trend-a-{uuid4().hex}")
    seed_fixture_scope(pg_engine, f"synthetic-s1-trend-b-{uuid4().hex}")
    monkeypatch.delenv("PAIYA_DEV_FIXTURE_MODE", raising=False)
    monkeypatch.delenv("PAIYA_READ_CONTEXT_GROUP_KEY", raising=False)

    response = client.get(
        "/v1/prices/trends",
        params={"model_key": "Synthetic item"},
        headers={"Host": "127.0.0.1:8137"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "read_scope_ambiguous"
    assert response.json()["detail"]["message"] == "Price trends require one active group grant"


def test_read_service_rechecks_a_revoked_resolved_grant(pg_engine):
    scope = seed_fixture_scope(pg_engine, f"synthetic-s1-revoked-{uuid4().hex}")
    grant = ReadableGrant(
        group_id=scope.group_id,
        grant_id=scope.grant_id,
        group_key=scope.group_key,
        group_source="fixture",
        source_kind="fixture",
    )
    with pg_engine.begin() as connection:
        connection.execute(
            sql_update(source_grants)
            .where(source_grants.c.id == scope.grant_id)
            .values(revoked_at=datetime.now(UTC))
        )

    with pg_engine.connect() as connection, pytest.raises(GrantUnavailable):
        read_posts(connection, grant)


def test_dev_fixture_context_requires_explicit_opt_in(monkeypatch):
    from paiya.read.context import dev_fixture_group_key_from_env, read_context_group_key_from_env

    monkeypatch.setenv("PAIYA_READ_CONTEXT_GROUP_KEY", "synthetic-demo")
    monkeypatch.delenv("PAIYA_DEV_FIXTURE_MODE", raising=False)
    assert read_context_group_key_from_env() == "synthetic-demo"
    assert dev_fixture_group_key_from_env() is None

    monkeypatch.setenv("PAIYA_DEV_FIXTURE_MODE", "1")
    assert dev_fixture_group_key_from_env() == "synthetic-demo"
    assert read_context_group_key_from_env() == "synthetic-demo"


def test_auth_challenge_returns_no_store_nonce(client):
    response = client.get(
        "/v1/auth/challenge",
        headers={"Host": "127.0.0.1:8137"},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["nonce"], str)
    assert response.headers["cache-control"] == "no-store"


def test_host_rebind_is_rejected(client):
    response = client.get("/healthz", headers={"Host": "attacker.example"})

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "origin_not_allowed"


def test_cross_origin_post_is_rejected(client):
    response = client.post(
        "/v1/auth/logout",
        headers={
            "Host": "127.0.0.1:8137",
            "Origin": "http://attacker.example",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "origin_not_allowed"


@pytest.mark.parametrize(
    "origin",
    [None, "null", "http://attacker.example", "http://127.0.0.1:8138", "http://localhost:8137"],
)
def test_unlock_rejects_invalid_origin_without_creating_session(
    origin, monkeypatch, tmp_path, pg_engine, client
):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)
    challenge = client.get("/v1/auth/challenge", headers={"Host": "127.0.0.1:8137"})
    assert challenge.status_code == 200
    headers = {"Host": "127.0.0.1:8137", "X-CSRF-Token": challenge.json()["nonce"]}
    if origin is not None:
        headers["Origin"] = origin

    response = client.post("/v1/auth/unlock", headers=headers, json={"passphrase": passphrase})

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "origin_not_allowed"
    with pg_engine.connect() as connection:
        assert connection.execute(select(owner_sessions)).all() == []


def test_unauthenticated_write_rejection_is_not_cacheable(client):
    response = client.post(
        "/v1/auth/logout",
        headers={
            "Host": "127.0.0.1:8137",
            "Origin": "http://127.0.0.1:8137",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_required"
    assert response.headers["cache-control"] == "no-store"


def test_unlock_without_pre_auth_csrf_is_rejected(monkeypatch, tmp_path, client):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)

    response = client.post(
        "/v1/auth/unlock",
        headers={
            "Host": "127.0.0.1:8137",
            "Origin": "http://127.0.0.1:8137",
        },
        json={"passphrase": passphrase},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_invalid"


def test_authenticated_write_rejects_non_ascii_csrf(client, authenticated_session):
    assert authenticated_session["session_id"]
    response = client.post(
        "/v1/auth/logout",
        headers=[
            (b"host", b"127.0.0.1:8137"),
            (b"origin", b"http://127.0.0.1:8137"),
            (b"x-csrf-token", b"\xff"),
        ],
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_invalid"


def test_unlock_rejects_non_ascii_pre_auth_csrf(client):
    challenge = client.get("/v1/auth/challenge", headers={"Host": "127.0.0.1:8137"})
    assert challenge.status_code == 200
    response = client.post(
        "/v1/auth/unlock",
        headers=[
            (b"host", b"127.0.0.1:8137"),
            (b"origin", b"http://127.0.0.1:8137"),
            (b"x-csrf-token", b"\xff"),
        ],
        json={"passphrase": "synthetic-passphrase"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_invalid"


def test_unlock_hashes_token_and_sets_contract_cookie(monkeypatch, tmp_path, pg_engine, client):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)
    challenge = client.get("/v1/auth/challenge", headers={"Host": "127.0.0.1:8137"})
    nonce = challenge.json()["nonce"]

    response = client.post(
        "/v1/auth/unlock",
        headers={
            "Host": "127.0.0.1:8137",
            "Origin": "http://127.0.0.1:8137",
            "X-CSRF-Token": nonce,
        },
        json={"passphrase": passphrase},
    )

    assert response.status_code == 200
    body = response.json()
    token = response.cookies.get("paiya_owner_session")
    cookie_header = response.headers["set-cookie"]
    assert body["session_id"]
    assert body["csrf_token"]
    assert token
    assert "HttpOnly" in cookie_header
    assert "SameSite=strict" in cookie_header
    assert "Path=/" in cookie_header
    assert "Max-Age=43200" in cookie_header
    assert "Domain=" not in cookie_header
    assert "; Secure" not in cookie_header
    with pg_engine.connect() as connection:
        stored = (
            connection.execute(
                select(owner_sessions).where(owner_sessions.c.id == body["session_id"])
            )
            .mappings()
            .one()
        )
    assert stored["token_hash"] == hashlib.sha256(token.encode("ascii")).digest()
    assert token.encode("ascii") != stored["token_hash"]


@pytest.mark.parametrize("expired_state", ["absolute", "idle"])
def test_expired_or_idle_session_returns_401(
    expired_state, monkeypatch, tmp_path, pg_engine, client
):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)
    response = unlock(client, passphrase)
    assert response.status_code == 200
    session_id = UUID(response.json()["session_id"])
    now = datetime.now(UTC)
    values = (
        {
            "created_at": now - timedelta(hours=13),
            "last_seen_at": now,
            "expires_at": now - timedelta(seconds=1),
        }
        if expired_state == "absolute"
        else {"last_seen_at": now - timedelta(minutes=31)}
    )
    with pg_engine.begin() as connection:
        connection.execute(
            sql_update(owner_sessions).where(owner_sessions.c.id == session_id).values(**values)
        )

    protected = client.get("/v1/auth/csrf", headers={"Host": "127.0.0.1:8137"})

    assert protected.status_code == 401
    assert protected.json()["detail"]["code"] == "session_required"


@pytest.mark.parametrize("csrf_token", [None, "wrong-csrf-token"])
def test_authenticated_write_rejects_missing_or_bad_csrf(csrf_token, monkeypatch, tmp_path, client):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)
    assert unlock(client, passphrase).status_code == 200
    headers = {
        "Host": "127.0.0.1:8137",
        "Origin": "http://127.0.0.1:8137",
    }
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token

    response = client.post("/v1/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_invalid"


def test_middleware_rejects_missing_csrf_before_route_dispatch(monkeypatch, tmp_path, client):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)
    assert unlock(client, passphrase).status_code == 200

    response = client.post(
        "/v1/not-a-route",
        headers={
            "Host": "127.0.0.1:8137",
            "Origin": "http://127.0.0.1:8137",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_invalid"


def test_logout_invalidates_session(monkeypatch, tmp_path, client):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)
    login = unlock(client, passphrase)
    csrf_token = login.json()["csrf_token"]
    session_token = login.cookies.get("paiya_owner_session")
    assert session_token

    logged_out = client.post(
        "/v1/auth/logout",
        headers={
            "Host": "127.0.0.1:8137",
            "Origin": "http://127.0.0.1:8137",
            "X-CSRF-Token": csrf_token,
        },
    )
    protected = client.get("/v1/auth/csrf", headers={"Host": "127.0.0.1:8137"})
    client.cookies.set("paiya_owner_session", session_token, domain="127.0.0.1", path="/")
    replayed = client.get(
        "/v1/auth/csrf",
        headers={"Host": "127.0.0.1:8137"},
    )
    client.cookies.delete("paiya_owner_session", domain="127.0.0.1", path="/")

    assert logged_out.status_code == 200
    assert "Max-Age=0" in logged_out.headers["set-cookie"]
    assert protected.status_code == 401
    assert protected.json()["detail"]["code"] == "session_required"
    assert replayed.status_code == 401
    assert replayed.json()["detail"]["code"] == "session_required"


def test_revoke_all_requires_csrf_and_revokes_every_session(
    monkeypatch, tmp_path, pg_engine, client
):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)
    login_a = unlock(client, passphrase)
    assert login_a.status_code == 200
    token_a = login_a.cookies["paiya_owner_session"]
    client.cookies.clear()
    login_b = unlock(client, passphrase)
    assert login_b.status_code == 200
    token_b = login_b.cookies["paiya_owner_session"]
    assert token_a != token_b
    client.cookies.clear()
    client.cookies.set("paiya_owner_session", token_a, domain="127.0.0.1", path="/")
    headers = {"Host": "127.0.0.1:8137", "Origin": "http://127.0.0.1:8137"}

    rejected = client.post("/v1/auth/revoke-all", headers=headers)
    assert rejected.status_code == 403
    assert rejected.json()["detail"]["code"] == "csrf_invalid"
    with pg_engine.connect() as connection:
        rows = connection.execute(select(owner_sessions)).mappings().all()
    assert len(rows) == 2
    assert all(row["revoked_at"] is None for row in rows)

    response = client.post(
        "/v1/auth/revoke-all", headers={**headers, "X-CSRF-Token": login_a.json()["csrf_token"]}
    )
    assert response.status_code == 200
    for token in (token_a, token_b):
        client.cookies.clear()
        client.cookies.set("paiya_owner_session", token, domain="127.0.0.1", path="/")
        read = client.get("/v1/auth/csrf", headers={"Host": "127.0.0.1:8137"})
        assert read.status_code == 401
    with pg_engine.connect() as connection:
        rows = connection.execute(select(owner_sessions)).mappings().all()
    assert len(rows) == 2
    assert all(row["revoked_at"] is not None for row in rows)


def test_auth_secrets_never_appear_in_logs(monkeypatch, tmp_path, client, caplog):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    canary = secrets.token_urlsafe(18)
    passphrase = f"passphrase-{canary}"
    nonce = f"preauth-{canary}"
    cookie_token = f"cookie-{canary}"
    csrf_token = f"csrf-{canary}"
    create_auth_verifier(passphrase)
    generated = iter((nonce, cookie_token, csrf_token))
    monkeypatch.setattr(secrets, "token_urlsafe", lambda _size: next(generated))

    with caplog.at_level(logging.DEBUG):
        login = unlock(client, passphrase)
        client.get("/v1/auth/csrf", headers={"Host": "127.0.0.1:8137"})
        client.post(
            "/v1/auth/logout",
            headers={
                "Host": "127.0.0.1:8137",
                "Origin": "http://127.0.0.1:8137",
                "X-CSRF-Token": csrf_token,
            },
        )

    assert login.status_code == 200
    assert login.cookies.get("paiya_owner_session") == cookie_token
    for secret in (canary, passphrase, nonce, cookie_token, csrf_token):
        assert secret not in caplog.text


def test_cli_stores_scrypt_verifier_outside_repository(monkeypatch, tmp_path, capsys):
    passphrase = secrets.token_urlsafe(18)
    answers = iter((passphrase, passphrase))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: next(answers))
    from paiya.auth import credentials

    monkeypatch.setattr(
        credentials.sys, "stdin", type("Terminal", (), {"isatty": lambda _self: True})()
    )

    from paiya.cli import main

    assert main(["auth", "set-passphrase"]) == 0
    auth_file = tmp_path / "Paiya" / "security" / "owner-auth.json"
    config_text = auth_file.read_text(encoding="utf-8")
    config = json.loads(config_text)

    assert config["kdf"] == "scrypt-v1"
    assert config["n"] == 2**15
    assert config["r"] == 8
    assert config["p"] == 1
    assert config["dklen"] == 32
    assert config["salt"] and config["verifier"]
    assert passphrase not in config_text
    assert passphrase not in capsys.readouterr().out


def test_serve_fails_closed_when_first_setup_has_no_terminal(monkeypatch, tmp_path, capsys):
    from paiya import serve
    from paiya.auth import credentials

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("PAIYA_DEV_FIXTURE_MODE", raising=False)
    monkeypatch.delenv("PAIYA_READ_CONTEXT_GROUP_KEY", raising=False)
    monkeypatch.setattr(
        credentials.sys, "stdin", type("Pipe", (), {"isatty": lambda _self: False})()
    )

    with patch.object(serve.uvicorn, "run") as run, pytest.raises(SystemExit) as error:
        serve.main()

    assert error.value.code == 2
    assert "interactive terminal" in capsys.readouterr().err
    run.assert_not_called()


def test_unlock_backoff_starts_after_five_failures(monkeypatch, tmp_path):
    from paiya.auth.credentials import (
        AuthenticationThrottled,
        create_auth_verifier,
        verify_owner_passphrase,
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    wrong_passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)

    for attempt in range(5):
        assert not verify_owner_passphrase(wrong_passphrase, now=100_000 + attempt)
    with pytest.raises(AuthenticationThrottled) as throttled:
        verify_owner_passphrase(passphrase, now=100_005)

    assert throttled.value.retry_after_seconds == 895
    assert verify_owner_passphrase(passphrase, now=100_900)


def test_unlock_endpoint_throttles_after_five_failed_attempts(monkeypatch, tmp_path, client):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    passphrase = secrets.token_urlsafe(18)
    wrong_passphrase = secrets.token_urlsafe(18)
    create_auth_verifier(passphrase)

    for _ in range(5):
        failed = unlock(client, wrong_passphrase)
        assert failed.status_code == 401
        assert failed.json()["detail"]["code"] == "session_required"
    blocked = unlock(client, passphrase)

    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "unlock_throttled"


def test_verifier_rejects_passphrase_shorter_than_twelve(monkeypatch, tmp_path):
    from paiya.auth.credentials import create_auth_verifier

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    with pytest.raises(ValueError, match="passphrase_too_short"):
        create_auth_verifier("short-pass")

    assert not (tmp_path / "Paiya" / "security" / "owner-auth.json").exists()
