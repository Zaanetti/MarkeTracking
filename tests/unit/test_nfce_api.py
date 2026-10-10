from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from marketracking.db.base import Base
from marketracking.db.models import FailedHtmlArtifact, FiscalDocument, Processing, Purchase
from marketracking.db.session import get_db
from marketracking.main import app
from marketracking.schemas.nfce import FiscalDocumentStatus
from marketracking.services.storage_service import StoredHtmlArtifact


def test_process_creates_then_returns_existing_purchase(
    consultation_url: str,
    sefaz_html: str,
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    portal_html = {"value": sefaz_html}

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    class FakePortalClient:
        def fetch(self, _consultation, *, on_attempt_start=None, on_attempt_finish=None) -> str:
            if on_attempt_start:
                on_attempt_start(1)
            if on_attempt_finish:
                on_attempt_finish(1, None)
            return portal_html["value"]

    monkeypatch.setattr(
        "marketracking.services.processing_service.SefazSpPortalClient", FakePortalClient
    )
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        created = client.post("/api/v1/nfce/process", data={"url": consultation_url})
        existing = client.post("/api/v1/nfce/process", data={"url": consultation_url})

        assert created.status_code == 201, created.text
        created_data = created.json()
        assert created_data["result"] == "created"
        assert created_data["already_processed"] is False
        assert created_data["purchase"]["total"]["payable_amount"] == "10.00"
        assert "consumer" not in str(created_data).lower()
        assert "123.456.789-00" not in str(created_data)
        assert "opaque%7Cconsultation-data" not in str(created_data)

        assert existing.status_code == 200
        assert existing.json()["result"] == "existing"
        assert existing.json()["already_processed"] is True

        processing_id = created_data["processamento_id"]
        trace = client.get(f"/api/v1/processings/{processing_id}")
        assert trace.status_code == 200
        trace_data = trace.json()
        assert trace_data["input_metadata"]["host"] == "www.nfce.fazenda.sp.gov.br"
        assert trace_data["input_metadata"]["access_key"] == created_data["purchase"][
            "fiscal_document"
        ]["access_key"]
        assert "opaque" not in str(trace_data)
        assert any(step["stage"] == "CONSULTANDO_PORTAL" for step in trace_data["steps"])

        portal_html["value"] = sefaz_html.replace("EMISSÃO NORMAL", "EMISSÃO CANCELADA")
        cancelled = client.post("/api/v1/nfce/process", data={"url": consultation_url})
        assert cancelled.status_code == 200
        assert cancelled.json()["result"] == "cancelled_existing"
        assert cancelled.json()["purchase"]["fiscal_document"]["status"] == "cancelled"

        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Purchase)) == 1
            assert session.scalar(select(func.count()).select_from(FiscalDocument)) == 1
            assert session.scalar(select(func.count()).select_from(Processing)) == 3
            assert session.scalar(select(Purchase.status)) == FiscalDocumentStatus.CANCELLED
            assert session.scalar(select(FiscalDocument.status)) == FiscalDocumentStatus.CANCELLED
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_process_requires_exactly_one_input() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/api/v1/nfce/process",
            data={"url": "https://example.com"},
            files={"image": ("receipt.png", b"not-read-because-both-inputs", "image/png")},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_input"
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_first_cancelled_document_is_rejected_without_purchase(
    consultation_url: str,
    sefaz_html: str,
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    cancelled_html = (
        sefaz_html.replace("EMISSÃO NORMAL", "EMISSÃO CANCELADA")
        .replace('id="tabResult"', 'id="unavailableItems"')
        .replace('id="totalNota"', 'id="unavailableTotals"')
    )

    class FakePortalClient:
        def fetch(self, _consultation, **_callbacks) -> str:
            return cancelled_html

    class FakeStorage:
        def store_failed_html(self, processing_id, html) -> StoredHtmlArtifact:
            return StoredHtmlArtifact(
                object_key=f"failed/{processing_id}.html.fernet",
                sha256=sha256(html.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )

    monkeypatch.setattr(
        "marketracking.services.processing_service.SefazSpPortalClient", FakePortalClient
    )
    monkeypatch.setattr("marketracking.services.processing_service.StorageService", FakeStorage)
    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post("/api/v1/nfce/process", data={"url": consultation_url})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "document_cancelled"
        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Purchase)) == 0
            assert session.scalar(select(func.count()).select_from(FiscalDocument)) == 0
            assert session.scalar(select(func.count()).select_from(FailedHtmlArtifact)) == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_old_web_interface_is_not_exposed() -> None:
    client = TestClient(app)

    assert client.get("/").status_code == 404
    assert client.get("/about").status_code == 404
