from fastapi.testclient import TestClient

from marketracking.main import app


def test_healthcheck_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "MarkeTracking"


def test_homepage_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "MarkeTracking" in response.text
