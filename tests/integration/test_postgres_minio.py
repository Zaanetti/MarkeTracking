from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from minio.commonconfig import Filter
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from marketracking.core.config import get_settings
from marketracking.db.models import FiscalDocument, Processing, Purchase, Store
from marketracking.parsers.sources.sefaz_sp import SefazSpNfceParser
from marketracking.schemas.nfce import CaptureMethod
from marketracking.services import persistence_service
from marketracking.services.normalization_service import normalize_nfce
from marketracking.services.persistence_service import (
    persist_authorized_purchase,
    start_processing,
)
from marketracking.services.storage_service import StorageService
from marketracking.main import app


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("MARKETRACKING_INTEGRATION_TESTS") != "1",
        reason="set MARKETRACKING_INTEGRATION_TESTS=1 with disposable services",
    ),
]


def test_postgres_concurrent_processing_creates_one_purchase(
    sefaz_html: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    access_key = _new_access_key()
    cnpj = f"9{uuid4().int % 10**13:013d}"
    raw_document = SefazSpNfceParser().parse(sefaz_html).model_copy(
        update={"access_key": access_key, "merchant_cnpj": cnpj}
    )
    purchase_data = normalize_nfce(raw_document, access_key)
    processing_ids = (uuid4(), uuid4())
    barrier = Barrier(2)
    original_insert = persistence_service._insert_purchase

    def synchronized_insert(db, processing_id: UUID, data):
        barrier.wait(timeout=20)
        return original_insert(db, processing_id, data)

    monkeypatch.setattr(persistence_service, "_insert_purchase", synchronized_insert)

    def persist(processing_id: UUID):
        with session_factory() as db:
            start_processing(
                db,
                processing_id=processing_id,
                capture_method=CaptureMethod.URL,
                input_access_key=access_key,
            )
            return persist_authorized_purchase(
                db,
                processing_id=processing_id,
                data=purchase_data,
            )

    try:
        readiness = TestClient(app).get("/api/ready")
        assert readiness.status_code == 200

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(persist, processing_ids))

        assert sorted(already_exists for _, already_exists in results) == [False, True]
        with session_factory() as db:
            assert db.scalar(
                select(func.count())
                .select_from(FiscalDocument)
                .where(FiscalDocument.access_key == access_key)
            ) == 1
            assert db.scalar(
                select(func.count())
                .select_from(Purchase)
                .join(Purchase.fiscal_document)
                .where(FiscalDocument.access_key == access_key)
            ) == 1
    finally:
        _cleanup_purchase(session_factory, access_key, cnpj, processing_ids)
        engine.dispose()


def test_minio_encrypted_artifact_retention_preserves_other_rules() -> None:
    settings = get_settings()
    service = StorageService()
    client = service.get_client()
    if not client.bucket_exists(service.bucket):
        client.make_bucket(service.bucket)
    client.set_bucket_lifecycle(
        service.bucket,
        LifecycleConfig(
            [
                Rule(
                    rule_id="integration-existing-rule",
                    status="Enabled",
                    rule_filter=Filter(prefix="archive/"),
                    expiration=Expiration(days=90),
                )
            ]
        ),
    )

    processing_id = uuid4()
    artifact = service.store_failed_html(
        processing_id,
        '<html><div class="jqm-collapsible"><h4>Consumidor</h4>'
        "<p>CPF 123.456.789-00</p></div><p>Falha no layout</p></html>",
    )
    response = client.get_object(service.bucket, artifact.object_key)
    try:
        ciphertext = response.read()
    finally:
        response.close()
        response.release_conn()

    plaintext = Fernet(settings.failed_html_encryption_key.encode("ascii")).decrypt(ciphertext)
    assert b"Falha no layout" in plaintext
    assert b"Consumidor" not in plaintext
    assert b"123.456.789-00" not in plaintext

    lifecycle = client.get_bucket_lifecycle(service.bucket)
    assert lifecycle is not None
    assert {rule.rule_id for rule in lifecycle.rules} == {
        "integration-existing-rule",
        "failed-html-retention",
    }
    assert artifact.expires_at is not None
    service.delete_failed_html(artifact.object_key)


def _new_access_key() -> str:
    first_43 = f"35{uuid4().int % 10**41:041d}"
    weighted_sum = sum(
        int(digit) * (2 + index % 8)
        for index, digit in enumerate(reversed(first_43))
    )
    remainder = weighted_sum % 11
    check_digit = 0 if remainder < 2 else 11 - remainder
    return f"{first_43}{check_digit}"


def _cleanup_purchase(session_factory, access_key: str, cnpj: str, processing_ids) -> None:
    with session_factory() as db:
        document = db.scalar(
            select(FiscalDocument).where(FiscalDocument.access_key == access_key)
        )
        if document is not None:
            db.delete(document)
            db.flush()
        store = db.scalar(select(Store).where(Store.cnpj == cnpj))
        if store is not None:
            db.delete(store)
        for processing_id in processing_ids:
            processing = db.get(Processing, processing_id)
            if processing is not None:
                db.delete(processing)
        db.commit()
