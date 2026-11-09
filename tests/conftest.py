"""Shared local HTTP access setup for the TestClient acceptance suite."""

import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def dev_fixture_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep existing fixture-backed API tests explicit and on the loopback port."""
    monkeypatch.setenv("PAIYA_DEV_FIXTURE_MODE", "1")
    monkeypatch.setenv("PAIYA_PORT", "8137")
    monkeypatch.delenv("PAIYA_READ_CONTEXT_GROUP_KEY", raising=False)

    original_init = TestClient.__init__

    def loopback_init(self: TestClient, app: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", "http://127.0.0.1:8137")
        original_init(self, app, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", loopback_init)


@pytest.fixture
def authenticated_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> dict[str, str]:
    """Unlock a real owner session for an HTTP test using the injected test database."""
    from paiya.auth.credentials import create_auth_verifier

    passphrase = secrets.token_urlsafe(18)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    create_auth_verifier(passphrase)
    host = "127.0.0.1:8137"
    challenge = client.get("/v1/auth/challenge", headers={"Host": host}).json()["nonce"]
    response = client.post(
        "/v1/auth/unlock",
        headers={
            "Host": host,
            "Origin": f"http://{host}",
            "X-CSRF-Token": challenge,
        },
        json={"passphrase": passphrase},
    )
    if response.status_code != 200:
        raise AssertionError(f"test owner session setup failed with HTTP {response.status_code}")
    body = response.json()
    return {
        "session_id": body["session_id"],
        "csrf_token": body["csrf_token"],
    }
