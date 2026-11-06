"""Contract test for the bare API health probe."""

from fastapi.testclient import TestClient

from paiya.api.app import app


def test_healthz() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
