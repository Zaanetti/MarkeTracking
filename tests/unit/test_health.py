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
    assert "Anexar imagem" in response.text
    assert "Coletar infos" in response.text
    assert "Digite os 44 digitos do cupom" not in response.text
    assert "Scaffold inicial" not in response.text


def test_about_page_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/about")

    assert response.status_code == 200
    assert "Scaffold inicial" in response.text


def test_qr_check_endpoint_returns_found_status(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "marketracking.web.routes.decode_qr_bytes",
        lambda _: "https://www.nfce.fazenda.sp.gov.br/exemplo",
    )

    response = client.post(
        "/qr/check",
        files={"receipt_image": ("cupom.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is True
    assert payload["qr_code"] == "https://www.nfce.fazenda.sp.gov.br/exemplo"


def test_collect_route_renders_extracted_receipt(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "marketracking.web.routes.collect_receipt_from_qr_url",
        lambda _: {
            "merchant_name": "Mercado Exemplo",
            "merchant_tax_id": "12.345.678/0001-99",
            "merchant_address": "Rua das Flores, 10, Sorocaba, SP",
            "document_number": "123",
            "document_series": "1",
            "issue_datetime": "09/04/2026 20:00:00",
            "authorization_protocol": "999999",
            "authorization_datetime": "09/04/2026 20:00:03",
            "xml_version": "4.00",
            "xslt_version": "2.05",
            "total_items": "2",
            "total_amount": "25,90",
            "total_taxes": "3,10",
            "consumer_description": "Consumidor nao identificado",
            "access_key": "3526 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000",
            "payments": [
                {"payment_method": "Cartao de Debito", "amount": "25,90"},
            ],
            "items": [
                {
                    "line_number": "1",
                    "product_code": "100",
                    "description": "Arroz",
                    "quantity": "1",
                    "unit": "UN",
                    "unit_price": "10,00",
                    "total_price": "10,00",
                }
            ],
            "additional_info": ["Volte sempre"],
        },
    )

    response = client.post(
        "/collect",
        data={"qr_code": "https://www.nfce.fazenda.sp.gov.br/exemplo"},
    )

    assert response.status_code == 200
    assert "Mercado Exemplo" in response.text
    assert "Arroz" in response.text
    assert "Volte sempre" in response.text
