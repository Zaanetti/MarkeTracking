from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marketracking.db.session import get_db
from marketracking.main import app


def test_healthcheck_returns_ok() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "MarkeTracking"


def test_readiness_checks_database() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/api/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_readiness_hides_database_failure_details() -> None:
    def override_get_db():
        class UnavailableDatabase:
            def execute(self, _statement):
                raise OperationalError("SELECT 1", {}, RuntimeError("connection refused"))

        yield UnavailableDatabase()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/api/ready")

        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert "connection refused" not in response.text
    finally:
        app.dependency_overrides.clear()
