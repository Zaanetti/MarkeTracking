from marketracking.schemas.nfce import FiscalDocumentStatus
from marketracking.parsers.sources.sefaz_sp import SefazSpNfceParser


def test_parses_document_items_and_totals_without_consumer_data(sefaz_html: str) -> None:
    document = SefazSpNfceParser().parse(sefaz_html)

    assert document.merchant_cnpj == "12345678000195"
    assert document.document_number == "123"
    assert document.document_series == "1"
    assert document.status == FiscalDocumentStatus.AUTHORIZED
    assert document.items[0].line_number == 1
    assert document.items[0].store_product_code == "1001"
    assert document.items[0].description == "Arroz tipo 1, pacote 5 kg"
    assert document.declared_line_count == "1"
    assert document.gross_items_amount == "10,00"
    assert document.total_discount == "0,00"
    assert document.payable_amount == "10,00"
    assert not hasattr(document, "consumer")
    assert "123.456.789-00" not in document.model_dump_json()


def test_missing_store_product_code_does_not_drop_purchase_item(sefaz_html: str) -> None:
    html = sefaz_html.replace('<span class="RCod">Código: 1001</span>', "")

    document = SefazSpNfceParser().parse(html)

    assert len(document.items) == 1
    assert document.items[0].store_product_code is None


def test_cancelled_document_can_be_classified_without_item_table(sefaz_html: str) -> None:
    html = sefaz_html.replace("EMISSÃO NORMAL", "EMISSÃO CANCELADA")
    html = html.replace('<table id="tabResult">', '<table id="otherTable">')
    html = html.replace('<div id="totalNota">', '<div id="otherTotals">')

    document = SefazSpNfceParser().parse(html)

    assert document.status == FiscalDocumentStatus.CANCELLED
    assert document.items == []


def test_parser_only_handles_the_official_https_host() -> None:
    parser = SefazSpNfceParser()

    assert parser.can_handle("https://www.nfce.fazenda.sp.gov.br/qrcode?p=payload")
    assert not parser.can_handle("http://www.nfce.fazenda.sp.gov.br/qrcode?p=payload")
    assert not parser.can_handle("https://www.nfce.fazenda.sp.gov.br.evil.example/")
    assert not parser.can_handle("https://evil.example/?next=nfce.fazenda.sp.gov.br")
