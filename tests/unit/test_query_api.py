from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marketracking.db.base import Base
from marketracking.db.models import (
    FiscalDocument,
    Processing,
    Purchase,
    PurchaseItem,
    PurchaseTotal,
    Store,
)
from marketracking.db.session import get_db
from marketracking.main import app
from marketracking.schemas.nfce import (
    CaptureMethod,
    FiscalDocumentStatus,
    ProcessingOutcome,
    ProcessingState,
)


TZ = ZoneInfo("America/Sao_Paulo")
DATE1 = datetime(2026, 10, 1, 12, 0, tzinfo=TZ)
DATE2 = datetime(2026, 10, 2, 23, 59, 59, tzinfo=TZ)
STORE_A = "11111111111111"
STORE_B = "22222222222222"
STORE_C = "33333333333333"


@pytest.fixture
def query_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with factory() as db:
        _seed(db)
    try:
        yield TestClient(app), factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _seed(db: Session) -> dict[str, object]:
    store_a = Store(
        cnpj=STORE_A,
        name="Mercado Atualizado",
        address="Rua atual, 10",
        details_as_of=DATE2,
    )
    store_b = Store(
        cnpj=STORE_B,
        name="Mercado Beta",
        address="Rua beta, 20",
        details_as_of=DATE2,
    )
    store_c = Store(
        cnpj=STORE_C,
        name="Mercado Sem Compras",
        address="Rua gama, 30",
        details_as_of=DATE2,
    )
    db.add_all([store_a, store_b, store_c])
    db.flush()
    ids = {}
    purchases = []
    specs = [
        # Same store code and purchase, two different source prices: observed average is 40/3.
        (store_a, DATE1, FiscalDocumentStatus.AUTHORIZED, Decimal("40.00"), Decimal("45.00"), Decimal("5.00"), Decimal("5.00"), [
            ("A1", "Maçã gala", "2", "10", "20.00"),
            ("A1", "Maçã fuji", "1", "20", "20.00"),
            (None, "Bread 100% integral", "1", "5", "5.00"),
            (None, "Maçã gala", "1", "0", "0.00"),
        ]),
        (store_a, datetime(2026, 10, 2, 10, 0, tzinfo=TZ), FiscalDocumentStatus.AUTHORIZED, Decimal("50.00"), Decimal("55.00"), Decimal("5.00"), None, [
            ("A1", "Maçã premium", "3", "15", "45.00"),
            (None, "Bread 100% integral", "2", "5", "10.00"),
        ]),
        (store_b, DATE2, FiscalDocumentStatus.AUTHORIZED, Decimal("30.00"), Decimal("30.00"), Decimal("0.00"), Decimal("2.00"), [
            ("A1", "Maçã", "1", "25", "25.00"),
            (None, "Suco", "1", "5", "5.00"),
        ]),
        (store_a, datetime(2026, 10, 2, 15, 0, tzinfo=TZ), FiscalDocumentStatus.CANCELLED, Decimal("999.00"), Decimal("999.00"), Decimal("0.00"), None, [
            ("A1", "Maçã cancelada", "1", "999", "999.00"),
        ]),
    ]
    for index, (store, issued_at, status, payable, gross, discount, taxes, line_specs) in enumerate(specs):
        document_id, purchase_id = uuid4(), uuid4()
        document = FiscalDocument(
            id=document_id,
            access_key=f"35{index:041d}0",
            store=store,
            number=str(index + 1),
            series="1",
            issued_at=issued_at,
            authorization_protocol=f"AUTH-{index}",
            authorized_at=issued_at,
            status=status,
        )
        purchase = Purchase(
            id=purchase_id,
            fiscal_document=document,
            store=store,
            status=status,
            issued_store_name=f"Nome da nota {index + 1}",
            issued_store_address=f"Endereço da nota {index + 1}",
            items=[
                PurchaseItem(
                    id=uuid4(),
                    line_number=line + 1,
                    store_product_code=code,
                    description=description,
                    quantity=Decimal(quantity),
                    commercial_unit="KG" if store is store_b and code == "A1" else "UN",
                    unit_price=Decimal(price),
                    line_total=Decimal(line_total),
                )
                for line, (code, description, quantity, price, line_total) in enumerate(line_specs)
            ],
            total=PurchaseTotal(
                declared_line_count=len(line_specs),
                gross_items_amount=gross,
                total_discount=discount,
                payable_amount=payable,
                total_taxes=taxes,
            ),
        )
        db.add(purchase)
        purchases.append(purchase)
        ids[f"purchase{index + 1}"] = purchase_id
    db.flush()
    for idx, (outcome, state, duration, purchase) in enumerate(
        [
            (ProcessingOutcome.CREATED, ProcessingState.COMPLETED, 100, purchases[0]),
            (ProcessingOutcome.EXISTING, ProcessingState.COMPLETED, 200, purchases[0]),
            (ProcessingOutcome.FAILED, ProcessingState.FAILED, 300, None),
            (ProcessingOutcome.REJECTED, ProcessingState.REJECTED, 400, None),
            (None, ProcessingState.FETCHING_PORTAL, None, None),
        ]
    ):
        created = datetime(2026, 10, 2, 9 + idx, 0, tzinfo=TZ)
        db.add(
            Processing(
                id=uuid4(),
                capture_method=CaptureMethod.URL,
                state=state,
                outcome=outcome,
                purchase_id=purchase.id if purchase else None,
                error_code="portal_unavailable" if state == ProcessingState.FAILED else None,
                error_message="Falha de consulta" if state == ProcessingState.FAILED else None,
                started_at=created,
                created_at=created,
                finished_at=created if duration is not None else None,
                duration_ms=duration,
            )
        )
    db.commit()
    return ids


def test_purchase_lists_details_filters_and_pagination(query_client) -> None:
    client, _ = query_client
    response = client.get("/api/v1/purchases", params={"date_from": "2026-10-02", "date_to": "2026-10-02"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"] == {"page": 1, "page_size": 25, "total": 3, "total_pages": 1}
    assert all(item["issued_at"].startswith("2026-10-02") for item in body["data"])
    assert sum(Decimal(item["payable_amount"]) for item in body["data"]) == Decimal("1079.00")

    cancelled = client.get("/api/v1/purchases", params={"status": "cancelled"}).json()
    assert cancelled["pagination"]["total"] == 1
    only_from = client.get("/api/v1/purchases", params={"date_from": "2026-10-02"}).json()
    assert only_from["pagination"]["total"] == 3
    only_to = client.get("/api/v1/purchases", params={"date_to": "2026-10-01"}).json()
    assert only_to["pagination"]["total"] == 1
    authorized = client.get("/api/v1/purchases", params={"status": "authorized"}).json()
    assert authorized["pagination"]["total"] == 3
    beyond = client.get("/api/v1/purchases", params={"page": 100}).json()
    assert beyond["data"] == []
    assert beyond["pagination"]["total"] == 4
    assert client.get("/api/v1/purchases", params={"min_total": "-1"}).status_code == 422
    assert client.get("/api/v1/purchases", params={"store_cnpj": "12"}).status_code == 422
    assert client.get("/api/v1/purchases", params={"date_from": "2026-10-03", "date_to": "2026-10-01"}).status_code == 422

    purchase_id = next(
        row["purchase_id"]
        for row in client.get("/api/v1/purchases").json()["data"]
        if row["store_cnpj"] == STORE_A
    )
    detail = client.get(f"/api/v1/purchases/{purchase_id}")
    assert detail.status_code == 200
    assert detail.json()["purchase_id"] == purchase_id
    assert detail.json()["store"]["name"] == "Mercado Atualizado"
    assert detail.json()["issued_store_details"]["name"].startswith("Nome da nota")
    assert [item["line_number"] for item in detail.json()["items"]] == sorted(
        item["line_number"] for item in detail.json()["items"]
    )
    assert client.get(f"/api/v1/purchases/{uuid4()}").status_code == 404


def test_items_store_and_processing_queries(query_client) -> None:
    client, _ = query_client
    items = client.get("/api/v1/purchase-items", params={"q": "100%"}).json()
    assert items["pagination"]["total"] == 2
    assert {row["description"] for row in items["data"]} == {"Bread 100% integral"}
    line = client.get("/api/v1/purchase-items", params={"status": "cancelled"}).json()
    assert line["pagination"]["total"] == 1
    assert client.get("/api/v1/purchase-items", params={"purchase_id": "bad"}).status_code == 422

    stores = client.get(
        "/api/v1/stores",
        params={"date_from": "2026-10-01", "date_to": "2026-10-02", "sort": "purchase_count_desc"},
    ).json()
    assert stores["pagination"]["total"] == 3
    assert stores["data"][0]["cnpj"] == STORE_A
    assert stores["data"][0]["summary"] == {
        "purchase_count": 2,
        "payable_amount": "90.00",
        "last_purchase_at": stores["data"][0]["summary"]["last_purchase_at"],
    }
    assert stores["data"][0]["summary"]["last_purchase_at"].startswith("2026-10-02T10:00:00")
    zero_store = client.get(f"/api/v1/stores/{STORE_C}").json()
    assert zero_store["summary"] == {"purchase_count": 0, "payable_amount": "0.00", "last_purchase_at": None}
    assert client.get("/api/v1/stores/123").status_code == 422

    processings = client.get("/api/v1/processings", params={"purchase_id": "bad"})
    assert processings.status_code == 422
    page = client.get("/api/v1/processings", params={"page_size": 2}).json()
    assert page["pagination"]["total"] == 5
    assert page["data"][0]["purchase_id"] is None
    assert "steps" not in page["data"][0]


def test_spending_overview_items_and_price_math_have_explicit_expected_values(query_client) -> None:
    client, _ = query_client
    period = {"date_from": "2026-10-01", "date_to": "2026-10-02"}
    spending = client.get("/api/v1/reports/spending", params={**period, "group_by": "store"})
    assert spending.status_code == 200, spending.text
    summary = spending.json()["summary"]
    assert summary == {
        "purchase_count": 3,
        "gross_items_amount": "130.00",
        "total_discount": "10.00",
        "payable_amount": "120.00",
        "average_ticket": "40.00",
        "item_line_count": 8,
        "store_count": 2,
        "known_taxes_amount": "7.00",
        "purchases_with_tax_info": 2,
    }

    overview = client.get("/api/v1/dashboards/overview", params=period)
    assert overview.status_code == 200, overview.text
    assert overview.json()["current"]["purchase_count"] == 3
    assert overview.json()["current"]["payable_amount"] == "120.00"
    assert overview.json()["current"]["tax_info_coverage_percent"] == "66.67"
    assert overview.json()["current"]["items_with_local_code_count"] == 4
    assert overview.json()["current"]["local_code_coverage_percent"] == "50.00"
    assert overview.json()["previous"]["purchase_count"] == 0
    assert overview.json()["comparison"]["payable_amount"]["delta_percent"] is None

    store_spending = client.get(
        "/api/v1/reports/spending", params={**period, "group_by": "store", "limit": 1}
    ).json()
    assert store_spending["data"][0]["store_cnpj"] == STORE_A
    assert store_spending["data"][0]["payable_amount"] == "90.00"
    assert store_spending["others"] == {
        "store_count": 1,
        "purchase_count": 1,
        "payable_amount": "30.00",
        "share_percent": "25.00",
    }

    ranked = client.get("/api/v1/dashboards/items", params=period)
    assert ranked.status_code == 200, ranked.text
    apple = next(
        row
        for row in ranked.json()["data"]
        if row["identity"]["store_cnpj"] == STORE_A
        and row["identity"]["store_product_code"] == "A1"
    )
    assert apple["description"] == "Maçã premium"
    assert apple["description_variants_count"] == 3
    assert apple["purchase_count"] == 2
    assert apple["quantity"] == "6.000000"
    assert apple["gross_line_amount"] == "85.00"
    assert apple["last_unit_price"] == "15.000000"
    apple_named_identities = [
        row
        for row in ranked.json()["data"]
        if row["identity"]["store_cnpj"] == STORE_A
        and (
            row["identity"]["store_product_code"] == "A1"
            or row["identity"]["description"] == "Maçã gala"
        )
    ]
    assert len(apple_named_identities) == 2
    assert {row["identity"]["kind"] for row in apple_named_identities} == {
        "store_code",
        "source_description",
    }
    store_b_apple = next(
        row
        for row in ranked.json()["data"]
        if row["identity"]["store_cnpj"] == STORE_B
        and row["identity"]["store_product_code"] == "A1"
    )
    assert store_b_apple["commercial_unit"] == "KG"
    assert client.get("/api/v1/dashboards/items", params={**period, "metric": "quantity"}).status_code == 422

    prices = client.get(
        "/api/v1/reports/prices",
        params={**period, "store_cnpj": STORE_A, "commercial_unit": "UN", "store_product_code": "A1"},
    )
    assert prices.status_code == 200, prices.text
    price_summary = prices.json()["summary"]
    assert price_summary["observation_count"] == 2
    assert price_summary["quantity"] == "6.000000"
    assert price_summary["minimum_unit_price"] == "13.333333"
    assert price_summary["maximum_unit_price"] == "15.000000"
    assert price_summary["weighted_average_unit_price"] == "14.166667"
    assert price_summary["delta"] == "1.666667"
    assert price_summary["delta_percent"] == "12.50"
    assert prices.json()["data"][0]["description"] == "Maçã fuji"

    changes = client.get(
        "/api/v1/dashboards/price-changes",
        params={**period, "store_cnpj": STORE_A, "commercial_unit": "UN", "q": "Maçã"},
    )
    assert changes.status_code == 200, changes.text
    result = next(
        row for row in changes.json()["data"] if row["identity"]["store_product_code"] == "A1"
    )
    assert result["current"]["observed_unit_price"] == "15.000000"
    assert result["previous"]["observed_unit_price"] == "13.333333"
    assert result["delta_percent"] == "12.50"


def test_openapi_publishes_all_query_and_dashboard_routes() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    expected = {
        "/api/v1/purchases",
        "/api/v1/purchases/{purchase_id}",
        "/api/v1/purchase-items",
        "/api/v1/stores",
        "/api/v1/stores/{cnpj}",
        "/api/v1/processings",
        "/api/v1/processings/{processamento_id}",
        "/api/v1/reports/spending",
        "/api/v1/reports/prices",
        "/api/v1/dashboards/overview",
        "/api/v1/dashboards/items",
        "/api/v1/dashboards/price-changes",
        "/api/v1/dashboards/processings",
    }
    assert expected <= set(paths)
    assert paths["/api/v1/dashboards/overview"]["get"]["responses"]["200"]["content"]
