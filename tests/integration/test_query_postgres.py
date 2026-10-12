from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import sessionmaker

from marketracking.core.config import get_settings
from marketracking.db.models import (
    FiscalDocument,
    Processing,
    Purchase,
    PurchaseItem,
    PurchaseTotal,
    Store,
)
from marketracking.main import app
from marketracking.schemas.nfce import (
    CaptureMethod,
    FiscalDocumentStatus,
    ProcessingOutcome,
    ProcessingState,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("MARKETRACKING_INTEGRATION_TESTS") != "1",
        reason="requires the disposable PostgreSQL service from scripts/test-integration.ps1",
    ),
]

TZ = ZoneInfo("America/Sao_Paulo")


def _access_key(index: int) -> str:
    first_43 = f"35{index:041d}"
    weighted_sum = sum(
        int(digit) * (2 + position % 8)
        for position, digit in enumerate(reversed(first_43))
    )
    remainder = weighted_sum % 11
    check_digit = 0 if remainder < 2 else 11 - remainder
    return f"{first_43}{check_digit}"


def test_migration_backfill_is_exact_and_preserves_history() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    config = Config("alembic.ini")
    store_id, fiscal_id, purchase_id = uuid4(), uuid4(), uuid4()
    linked_id, image_id, failed_id = uuid4(), uuid4(), uuid4()
    access_key = _access_key(701)
    cnpj = f"8{uuid4().int % 10**13:013d}"
    try:
        command.downgrade(config, "20260408_0001")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stores (id, cnpj, name, address, details_as_of) "
                    "VALUES (:id, :cnpj, 'Synthetic store', 'Synthetic address', :at)"
                ),
                {"id": store_id, "cnpj": cnpj, "at": datetime(2026, 10, 1, tzinfo=TZ)},
            )
            connection.execute(
                text(
                    "INSERT INTO fiscal_documents "
                    "(id, access_key, store_id, number, series, issued_at, "
                    "authorization_protocol, authorized_at, status) "
                    "VALUES (:id, :key, :store, '1', '1', :at, 'SYNTHETIC', :at, 'authorized')"
                ),
                {
                    "id": fiscal_id,
                    "key": access_key,
                    "store": store_id,
                    "at": datetime(2026, 10, 1, 12, tzinfo=TZ),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO purchases "
                    "(id, fiscal_document_id, store_id, status, issued_store_name, issued_store_address) "
                    "VALUES (:id, :fiscal, :store, 'authorized', 'Synthetic issued name', 'Synthetic issued address')"
                ),
                {"id": purchase_id, "fiscal": fiscal_id, "store": store_id},
            )
            processing_rows = [
                {
                    "id": linked_id,
                    "capture": "url",
                    "state": "CONCLUIDO",
                    "outcome": "created",
                    "key": access_key,
                },
                {
                    "id": image_id,
                    "capture": "image",
                    "state": "CONCLUIDO",
                    "outcome": "existing",
                    "key": None,
                },
                {
                    "id": failed_id,
                    "capture": "url",
                    "state": "FALHA",
                    "outcome": "created",
                    "key": access_key,
                },
            ]
            connection.execute(
                text(
                    "INSERT INTO processing_records (id, capture_method, state, outcome, input_access_key) "
                    "VALUES (:id, :capture, :state, :outcome, :key)"
                ),
                processing_rows,
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            linked = connection.scalar(
                text("SELECT purchase_id FROM processing_records WHERE id=:id"), {"id": linked_id}
            )
            image_link = connection.scalar(
                text("SELECT purchase_id FROM processing_records WHERE id=:id"), {"id": image_id}
            )
            failed_link = connection.scalar(
                text("SELECT purchase_id FROM processing_records WHERE id=:id"), {"id": failed_id}
            )
            assert linked == purchase_id
            assert image_link is None
            assert failed_link is None
            assert connection.scalar(
                text("SELECT count(*) FROM purchases WHERE id=:id"), {"id": purchase_id}
            ) == 1

        # Exercise downgrade and a second upgrade on the same rows.
        command.downgrade(config, "20260408_0001")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT purchase_id FROM processing_records WHERE id=:id"),
                {"id": linked_id},
            ) == purchase_id
            assert connection.scalar(
                text("SELECT count(*) FROM processing_records")
            ) == 3
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM processing_records WHERE id = ANY(:ids)"),
                {"ids": [linked_id, image_id, failed_id]},
            )
            connection.execute(text("DELETE FROM purchases WHERE id=:id"), {"id": purchase_id})
            connection.execute(
                text("DELETE FROM fiscal_documents WHERE id=:id"), {"id": fiscal_id}
            )
            connection.execute(text("DELETE FROM stores WHERE id=:id"), {"id": store_id})
        engine.dispose()


def test_postgres_aggregates_timezone_price_identity_and_processing_percentiles() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    client = TestClient(app)
    stores = [
        Store(
            cnpj=f"7{uuid4().int % 10**13:013d}",
            name=f"Synthetic store {index}",
            address="Synthetic address",
            details_as_of=datetime(2026, 10, 2, tzinfo=TZ),
        )
        for index in range(2)
    ]
    purchase_ids = [uuid4() for _ in range(4)]
    processing_ids = [uuid4() for _ in range(5)]
    fiscal_ids = [uuid4() for _ in range(4)]
    store_ids = []
    try:
        with factory() as db:
            db.add_all(stores)
            db.flush()
            store_ids = [store.id for store in stores]
            # This first purchase occurs at 23:30 local, which is already the next UTC day.
            specs = [
                (0, datetime(2026, 10, 1, 23, 30, tzinfo=TZ), "authorized", "40.00", "45.00", "5.00", "5.00", [
                    ("A1", "Maçã gala", "2", "10", "20.00"),
                    ("A1", "Maçã fuji", "1", "20", "20.00"),
                    (None, "Bread 100% integral", "1", "5", "5.00"),
                    (None, "Maçã gala", "1", "0", "0.00"),
                ]),
                (0, datetime(2026, 10, 2, 10, 0, tzinfo=TZ), "authorized", "50.00", "55.00", "5.00", None, [
                    ("A1", "Maçã premium", "3", "15", "45.00"),
                    (None, "Bread 100% integral", "2", "5", "10.00"),
                ]),
                (1, datetime(2026, 10, 2, 23, 59, 59, 999999, tzinfo=TZ), "authorized", "30.00", "30.00", "0.00", "2.00", [
                    ("A1", "Maçã", "1", "25", "25.00"),
                    (None, "Suco", "1", "5", "5.00"),
                ]),
                (0, datetime(2026, 10, 2, 15, 0, tzinfo=TZ), "cancelled", "999.00", "999.00", "0.00", None, [
                    ("A1", "Maçã cancelada", "1", "999", "999.00"),
                ]),
            ]
            purchases = []
            for index, (store_index, issued_at, status, payable, gross, discount, taxes, lines) in enumerate(specs):
                store = stores[store_index]
                document = FiscalDocument(
                    id=fiscal_ids[index],
                    access_key=_access_key(710 + index),
                    store=store,
                    number=str(index + 1),
                    series="1",
                    issued_at=issued_at,
                    authorization_protocol=f"SYNTHETIC-{index}",
                    authorized_at=issued_at,
                    status=FiscalDocumentStatus(status),
                )
                purchase = Purchase(
                    id=purchase_ids[index],
                    fiscal_document=document,
                    store=store,
                    status=FiscalDocumentStatus(status),
                    issued_store_name=f"Issued name {index}",
                    issued_store_address=f"Issued address {index}",
                    items=[
                        PurchaseItem(
                            line_number=line_no,
                            store_product_code=code,
                            description=description,
                            quantity=Decimal(quantity),
                            commercial_unit="KG" if store is stores[1] and code == "A1" else "UN",
                            unit_price=Decimal(unit_price),
                            line_total=Decimal(line_total),
                        )
                        for line_no, (code, description, quantity, unit_price, line_total)
                        in enumerate(lines, start=1)
                    ],
                    total=PurchaseTotal(
                        declared_line_count=len(lines),
                        gross_items_amount=Decimal(gross),
                        total_discount=Decimal(discount),
                        payable_amount=Decimal(payable),
                        total_taxes=Decimal(taxes) if taxes is not None else None,
                    ),
                )
                db.add(purchase)
                purchases.append(purchase)
            db.flush()
            processing_specs = [
                (ProcessingOutcome.CREATED, ProcessingState.COMPLETED, 100, purchase_ids[0], None),
                (ProcessingOutcome.EXISTING, ProcessingState.COMPLETED, 200, purchase_ids[0], None),
                (ProcessingOutcome.FAILED, ProcessingState.FAILED, 300, None, "portal_unavailable"),
                (ProcessingOutcome.REJECTED, ProcessingState.REJECTED, 400, None, "invalid_access_key"),
                (None, ProcessingState.FETCHING_PORTAL, None, None, None),
            ]
            for index, (outcome, state, duration, purchase_id, error_code) in enumerate(processing_specs):
                created_at = datetime(2026, 10, 2, 9 + index, 0, tzinfo=TZ)
                db.add(
                    Processing(
                        id=processing_ids[index],
                        capture_method=CaptureMethod.URL,
                        state=state,
                        outcome=outcome,
                        purchase_id=purchase_id,
                        error_code=error_code,
                        error_message="Synthetic failure" if error_code else None,
                        started_at=created_at,
                        created_at=created_at,
                        finished_at=created_at if duration is not None else None,
                        duration_ms=duration,
                    )
                )
            # Larger synthetic issue-date spread for an EXPLAIN of the indexed range query.
            db.add_all(
                [
                    FiscalDocument(
                        id=uuid4(),
                        access_key=_access_key(10000 + index),
                        store=stores[0],
                        number=f"PLAN-{index}",
                        series="1",
                        issued_at=datetime(2020, 1, 1, tzinfo=TZ) + timedelta(days=index % 2500),
                        authorization_protocol="SYNTHETIC-PLAN",
                        authorized_at=datetime(2020, 1, 1, tzinfo=TZ) + timedelta(days=index % 2500),
                        status=FiscalDocumentStatus.AUTHORIZED,
                    )
                    for index in range(1500)
                ]
            )
            db.commit()

        with engine.begin() as connection:
            connection.execute(text("ANALYZE fiscal_documents"))
            plan = connection.execute(
                text(
                    "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM fiscal_documents "
                    "WHERE issued_at >= :start AND issued_at < :end "
                    "ORDER BY issued_at, id LIMIT 25"
                ),
                {
                    "start": datetime(2026, 10, 1, tzinfo=TZ),
                    "end": datetime(2026, 10, 4, tzinfo=TZ),
                },
            ).scalars().all()
            assert any("Execution Time" in line for line in plan)

        params = {"date_from": "2026-10-01", "date_to": "2026-10-03"}
        spending = client.get("/api/v1/reports/spending", params=params)
        assert spending.status_code == 200, spending.text
        assert spending.json()["summary"]["purchase_count"] == 3
        assert spending.json()["summary"]["payable_amount"] == "120.00"
        assert [item["payable_amount"] for item in spending.json()["data"]] == [
            "40.00",
            "80.00",
            "0.00",
        ]
        assert sum(item["purchase_count"] for item in spending.json()["data"]) == 3

        weekly_spending = client.get(
            "/api/v1/reports/spending",
            params={**params, "group_by": "week"},
        )
        assert weekly_spending.status_code == 200, weekly_spending.text
        assert len(weekly_spending.json()["data"]) == 1
        assert weekly_spending.json()["data"][0]["bucket_start"] == "2026-10-01"
        assert weekly_spending.json()["data"][0]["bucket_end"] == "2026-10-03"
        assert weekly_spending.json()["data"][0]["payable_amount"] == "120.00"

        price = client.get(
            "/api/v1/reports/prices",
            params={
                **params,
                "store_cnpj": stores[0].cnpj,
                "commercial_unit": "UN",
                "store_product_code": "A1",
                "bucket": "day",
            },
        )
        assert price.status_code == 200, price.text
        assert [item["observed_unit_price"] for item in price.json()["data"]] == [
            "13.333333",
            "15.000000",
            None,
        ]
        assert price.json()["summary"]["weighted_average_unit_price"] == "14.166667"
        weekly_prices = client.get(
            "/api/v1/reports/prices",
            params={
                **params,
                "store_cnpj": stores[0].cnpj,
                "commercial_unit": "UN",
                "store_product_code": "A1",
                "bucket": "week",
            },
        )
        assert weekly_prices.status_code == 200, weekly_prices.text
        assert weekly_prices.json()["data"][0]["bucket_start"] == "2026-10-01"
        assert weekly_prices.json()["data"][0]["bucket_end"] == "2026-10-03"
        assert weekly_prices.json()["data"][0]["observed_unit_price"] == "14.166667"

        ranked_items = client.get("/api/v1/dashboards/items", params=params)
        assert ranked_items.status_code == 200, ranked_items.text
        same_description = [
            row
            for row in ranked_items.json()["data"]
            if row["identity"]["store_cnpj"] == stores[0].cnpj
            and (
                row["identity"]["store_product_code"] == "A1"
                or row["identity"]["description"] == "Maçã gala"
            )
        ]
        assert len(same_description) == 2
        assert {row["identity"]["kind"] for row in same_description} == {
            "store_code",
            "source_description",
        }
        beta_apple = next(
            row
            for row in ranked_items.json()["data"]
            if row["identity"]["store_cnpj"] == stores[1].cnpj
            and row["identity"]["store_product_code"] == "A1"
        )
        assert beta_apple["commercial_unit"] == "KG"

        changes = client.get(
            "/api/v1/dashboards/price-changes",
            params={
                "date_from": "2026-10-02",
                "date_to": "2026-10-02",
                "store_cnpj": stores[0].cnpj,
                "commercial_unit": "UN",
                "q": "Maçã",
            },
        )
        assert changes.status_code == 200, changes.text
        apple_change = next(
            row for row in changes.json()["data"] if row["identity"]["store_product_code"] == "A1"
        )
        assert apple_change["previous"]["purchase_id"] == str(purchase_ids[0])
        assert apple_change["current"]["observed_unit_price"] == "15.000000"
        assert apple_change["delta_percent"] == "12.50"

        processings = client.get(
            "/api/v1/dashboards/processings",
            params={"date_from": "2026-10-02", "date_to": "2026-10-03", "bucket": "week"},
        )
        assert processings.status_code == 200, processings.text
        assert processings.json()["summary"] == {
            "processing_count": 5,
            "successful_count": 2,
            "failed_count": 1,
            "rejected_count": 1,
            "in_progress_count": 1,
            "existing_return_count": 1,
            "success_rate_percent": "50.00",
            "duration_p50_ms": "250.00",
            "duration_p95_ms": "385.00",
        }
        assert processings.json()["data"][0]["bucket_start"] == "2026-10-02"
        assert processings.json()["data"][0]["bucket_end"] == "2026-10-03"
    finally:
        with factory() as db:
            db.execute(delete(Processing).where(Processing.id.in_(processing_ids)))
            db.execute(delete(Purchase).where(Purchase.id.in_(purchase_ids)))
            db.execute(delete(FiscalDocument).where(FiscalDocument.id.in_(fiscal_ids)))
            db.execute(
                delete(FiscalDocument).where(
                    FiscalDocument.authorization_protocol == "SYNTHETIC-PLAN"
                )
            )
            if store_ids:
                db.execute(delete(Store).where(Store.id.in_(store_ids)))
            db.commit()
        engine.dispose()
