from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx
from bs4 import BeautifulSoup


class ReceiptCollectionError(Exception):
    """Raised when the NFC-e page cannot be fetched or parsed."""


def clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def normalize_text(value: str) -> str:
    cleaned = clean_text(value)
    normalized = unicodedata.normalize("NFKD", cleaned)
    return normalized.encode("ascii", "ignore").decode("ascii")


def find_section_content(soup: BeautifulSoup, title: str) -> str:
    normalized_title = normalize_text(title)
    for section in soup.select("#infos [data-role='collapsible']"):
        heading = section.find("h4")
        if heading and normalize_text(heading.get_text()) == normalized_title:
            item = section.find("li")
            if item:
                return item.decode_contents()
    raise ReceiptCollectionError(f"Bloco '{title}' nao encontrado na NFC-e.")


def content_text(html_fragment: str) -> str:
    fragment = BeautifulSoup(html_fragment, "html.parser")
    return clean_text(fragment.get_text(" ", strip=True))


def fetch_nfce_html(qr_url: str) -> str:
    if not qr_url.startswith(("http://", "https://")):
        raise ReceiptCollectionError("O QR Code encontrado nao contem uma URL valida.")

    try:
        response = httpx.get(
            qr_url,
            follow_redirects=True,
            timeout=20.0,
            headers={"user-agent": "Mozilla/5.0 MarkeTracking"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ReceiptCollectionError("Nao foi possivel consultar a pagina publica da NFC-e.") from exc

    return response.text


def parse_general_info(section_html: str) -> dict[str, str | None]:
    section_text = content_text(section_html)
    normalized_section = normalize_text(section_text)

    number_match = re.search(r"Numero:\s*(\d+)", normalized_section, flags=re.IGNORECASE)
    series_match = re.search(r"Serie:\s*(\d+)", normalized_section, flags=re.IGNORECASE)
    issue_match = re.search(
        r"Emissao:\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})",
        normalized_section,
    )
    protocol_match = re.search(
        r"Protocolo de Autorizacao:\s*(\d+)\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})",
        normalized_section,
        flags=re.IGNORECASE,
    )
    xml_match = re.search(r"Versao XML:\s*([\d.]+)", normalized_section, flags=re.IGNORECASE)
    xslt_match = re.search(r"Versao XSLT:\s*([\d.]+)", normalized_section, flags=re.IGNORECASE)

    return {
        "issue_type": "EMISSAO NORMAL" if "EMISSAO NORMAL" in normalized_section.upper() else None,
        "document_number": number_match.group(1) if number_match else None,
        "document_series": series_match.group(1) if series_match else None,
        "issue_datetime": issue_match.group(1) if issue_match else None,
        "authorization_protocol": protocol_match.group(1) if protocol_match else None,
        "authorization_datetime": protocol_match.group(2) if protocol_match else None,
        "document_view": "Via Consumidor" if "Via Consumidor" in section_text else None,
        "xml_version": xml_match.group(1) if xml_match else None,
        "xslt_version": xslt_match.group(1) if xslt_match else None,
    }


def parse_items(soup: BeautifulSoup) -> list[dict[str, str | None]]:
    items: list[dict[str, str | None]] = []

    for index, row in enumerate(soup.select("#tabResult tr"), start=1):
        description = row.select_one("td span.txtTit")
        product_code = row.select_one("td span.RCod")
        quantity = row.select_one("td span.Rqtd")
        unit = row.select_one("td span.RUN")
        unit_price = row.select_one("td span.RvlUnit")
        total_price = row.select_one("td span.valor")

        if not all([description, product_code, quantity, unit, unit_price, total_price]):
            continue

        code_match = re.search(
            r"Codigo:\s*(\d+)",
            normalize_text(product_code.get_text(" ", strip=True)),
            flags=re.IGNORECASE,
        )
        unit_price_match = re.search(r"Vl\. Unit\.:?\s*([\d,]+)", unit_price.get_text(" ", strip=True))

        items.append(
            {
                "line_number": str(index),
                "product_code": code_match.group(1) if code_match else None,
                "description": clean_text(description.get_text(" ", strip=True)),
                "quantity": clean_text(quantity.get_text(" ", strip=True)).replace("Qtde.:", "").strip(),
                "unit": clean_text(unit.get_text(" ", strip=True)).replace("UN:", "").strip(),
                "unit_price": unit_price_match.group(1) if unit_price_match else None,
                "total_price": clean_text(total_price.get_text(" ", strip=True)),
            }
        )

    return items


def parse_totals(soup: BeautifulSoup) -> dict[str, Any]:
    total_note = soup.select_one("#totalNota")
    if total_note is None:
        raise ReceiptCollectionError("Bloco de totais nao encontrado na NFC-e.")

    total_items = None
    total_amount = None
    total_taxes = None
    payments: list[dict[str, str | None]] = []

    for block in total_note.find_all("div", recursive=False):
        label = block.find("label")
        amount = block.find("span", class_="totalNumb")
        if label is None or amount is None:
            continue

        label_text = clean_text(label.get_text(" ", strip=True))
        normalized_label = normalize_text(label_text)
        amount_text = clean_text(amount.get_text(" ", strip=True))

        if normalized_label.startswith("Qtd. total de itens"):
            total_items = amount_text
            continue
        if normalized_label.startswith("Valor a pagar"):
            total_amount = amount_text
            continue
        if normalized_label.startswith("Forma de pagamento"):
            continue
        if "Tributos Totais Incidentes" in normalized_label:
            total_taxes = amount_text
            continue

        payments.append(
            {
                "payment_method": label_text,
                "amount": None if amount_text.lower() == "nan" else amount_text,
            }
        )

    return {
        "total_items": total_items,
        "total_amount": total_amount,
        "total_taxes": total_taxes,
        "payments": payments,
    }


def collect_receipt_from_qr_url(qr_url: str) -> dict[str, Any]:
    html = fetch_nfce_html(qr_url)
    soup = BeautifulSoup(html, "html.parser")

    merchant_name_node = soup.select_one("#u20")
    text_blocks = soup.select("#conteudo .txtCenter > div.text")
    if merchant_name_node is None or len(text_blocks) < 2:
        raise ReceiptCollectionError("A pagina retornada nao possui o layout esperado de NFC-e.")

    merchant_name = clean_text(merchant_name_node.get_text(" ", strip=True))
    merchant_tax_id = clean_text(text_blocks[0].get_text(" ", strip=True)).replace("CNPJ:", "").strip()
    merchant_address = clean_text(text_blocks[1].get_text(" ", strip=True))

    general_info = parse_general_info(find_section_content(soup, "Informacoes gerais da Nota"))
    access_key_html = find_section_content(soup, "Chave de acesso")
    access_key_text = content_text(access_key_html)
    access_key_match = re.search(r"Chave de acesso:\s*(.*)", access_key_text, flags=re.IGNORECASE)
    access_key = access_key_match.group(1) if access_key_match else None
    consumer_description = content_text(find_section_content(soup, "Consumidor"))
    additional_info_raw = content_text(find_section_content(soup, "Informacoes de interesse do contribuinte"))
    totals = parse_totals(soup)
    items = parse_items(soup)

    return {
        "source_url": qr_url,
        "merchant_name": merchant_name,
        "merchant_tax_id": merchant_tax_id,
        "merchant_address": merchant_address,
        "access_key": access_key,
        "consumer_description": consumer_description,
        "additional_info": [part for part in additional_info_raw.split("|") if part],
        "items": items,
        "payments": totals["payments"],
        "total_items": totals["total_items"],
        "total_amount": totals["total_amount"],
        "total_taxes": totals["total_taxes"],
        **general_info,
    }
