"""Remote owner access must pass the trusted Vercel gateway."""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from paiya.api import auth as auth_api
from paiya.api.app import app
from paiya.auth.mobile_gateway import (
    DEVICE_COOKIE,
    DEVICE_MAX_AGE_SECONDS,
    check_preview_database_url,
    mobile_gateway,
)

ORIGIN = "https://paiya-preview.example.test"
BACKEND_HOST = "paiya-api.example.test"
PROXY_KEY = "a" * 64
PAIRING_CODE = "b" * 64
TRUSTED = {"Host": BACKEND_HOST, "X-Paiya-Proxy-Key": PROXY_KEY}


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.delenv("PAIYA_DEV_FIXTURE_MODE", raising=False)
    monkeypatch.setenv(
        "PAIYA_DATABASE_URL", "postgresql+psycopg://paiya_o5:synthetic@db:5432/paiya_o5_synthetic"
    )
    monkeypatch.setenv("PAIYA_O5_ORIGIN", ORIGIN)
    monkeypatch.setenv("PAIYA_O5_BACKEND_HOST", BACKEND_HOST)
    monkeypatch.setenv("PAIYA_O5_PROXY_KEY", PROXY_KEY)
    monkeypatch.setenv("PAIYA_O5_PAIRING_CODE", PAIRING_CODE)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def test_trusted_gateway_serves_a_no_store_challenge(gateway):
    response = gateway.get("/v1/auth/challenge", headers=TRUSTED)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("host", [BACKEND_HOST, "127.0.0.1:8137"])
def test_gateway_rejects_direct_requests_including_loopback_headers(gateway, host):
    response = gateway.get("/v1/auth/challenge", headers={"Host": host})
    assert response.status_code == 403


def test_valid_gateway_does_not_accept_wrong_browser_origin(gateway):
    response = gateway.post("/v1/auth/unlock", headers={**TRUSTED, "Origin": "https://other.test"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "origin_not_allowed"


def test_valid_gateway_reaches_csrf_check_for_approved_origin(gateway):
    response = gateway.post("/v1/auth/unlock", headers={**TRUSTED, "Origin": ORIGIN})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_invalid"


def test_gateway_needs_device_cookie_before_session_resolution(gateway):
    response = gateway.get("/v1/posts", headers=TRUSTED)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_gateway_limits_exposure_to_versioned_api(gateway):
    response = gateway.get("/docs", headers=TRUSTED)
    assert response.status_code == 404


def test_pairing_code_rotation_rejects_previous_device_before_db_read(gateway, monkeypatch):
    settings = mobile_gateway()
    assert settings is not None
    gateway.cookies.set(DEVICE_COOKIE, settings.issue_device_token())
    monkeypatch.setenv("PAIYA_O5_PAIRING_CODE", "c" * 64)
    assert gateway.get("/v1/posts", headers=TRUSTED).status_code == 401


def test_gateway_rejects_the_loopback_fixture_login_bypass(gateway, monkeypatch):
    monkeypatch.setenv("PAIYA_DEV_FIXTURE_MODE", "1")
    with pytest.raises(ValueError, match="fixture login bypass"):
        mobile_gateway()


def test_gateway_rejects_an_incomplete_configuration(gateway, monkeypatch):
    monkeypatch.delenv("PAIYA_O5_PROXY_KEY")
    with pytest.raises(ValueError, match="distinct 32-byte keys"):
        mobile_gateway()


@pytest.mark.parametrize("database", ["paiya_b2", "paiya_o5_synthetic?host=127.0.0.1"])
def test_preview_rejects_a_different_database_or_host_override(gateway, monkeypatch, database):
    monkeypatch.setenv("PAIYA_DATABASE_URL", f"postgresql+psycopg://paiya_o5:x@db:5432/{database}")
    with pytest.raises(ValueError, match="dedicated Compose synthetic database"):
        check_preview_database_url()


def test_preview_rejects_non_auth_writes(gateway):
    response = gateway.post("/v1/imports", headers={**TRUSTED, "Origin": ORIGIN})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "write_forbidden"


def test_device_token_expiry_is_enforced_by_the_server(gateway):
    settings = mobile_gateway()
    assert settings is not None
    token = settings.issue_device_token(now=1000)
    request = Request(
        {"type": "http", "headers": [(b"cookie", f"{DEVICE_COOKIE}={token}".encode())]}
    )
    assert settings.accepts_device(request, now=1000 + DEVICE_MAX_AGE_SECONDS - 1)
    assert not settings.accepts_device(request, now=1000 + DEVICE_MAX_AGE_SECONDS)


def test_unpaired_requests_do_not_spend_the_passphrase_attempt_budget(gateway, monkeypatch):
    def verify(_passphrase):
        pytest.fail("An unpaired API request reached owner passphrase verification")

    monkeypatch.setattr(auth_api, "verify_owner_passphrase", verify)
    challenge = gateway.get("/v1/auth/challenge", headers=TRUSTED).json()
    assert challenge["pairing_required"] is True
    response = gateway.post(
        "/v1/auth/unlock",
        headers={**TRUSTED, "Origin": ORIGIN, "X-CSRF-Token": challenge["nonce"]},
        json={"passphrase": "synthetic-password", "pairing_code": "wrong"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "pairing_required"
