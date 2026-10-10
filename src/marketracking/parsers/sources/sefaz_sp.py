from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from marketracking.schemas.nfce import (
    ErrorCode,
    FiscalDocumentStatus,
    ProcessingState,
    RawNfceDocument,
    RawPurchaseItem,
)
from marketracking.services.errors import ProcessingFailure


_CNPJ = re.compile(r"(?<!\d)(\d{2}\D?\d{3}\D?\d{3}\D?\d{4}\D?\d{2})(?!\d)")
_ACCESS_KEY = re.compile(r"(?<!\d)(?:\d[\s.\-]*){43}\d(?!\d)")
_NUMBER = re.compile(r"\d[\d.]*,\d+|\d+(?:[.,]\d+)?")


def clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", clean_text(value))
    return normalized.encode("ascii", "ignore").decode("ascii").lower()


class SefazSpNfceParser:
    name = "sefaz-sp-nfce-v1"

    def can_handle(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        return parsed.scheme.lower() == "https" and parsed.hostname is not None and (
            parsed.hostname.lower() == "www.nfce.fazenda.sp.gov.br"
        )

    def parse(self, html: str) -> RawNfceDocument:
        soup = BeautifulSoup(html, "html.parser")
        merchant_name_node = soup.select_one("#u20")
        text_blocks = soup.select("#conteudo .txtCenter > div.text")
        item_table = soup.select_one("#tabResult")
        totals_node = soup.select_one("#totalNota")
        if merchant_name_node is None or len(text_blocks) < 2:
            raise _failure(
                ErrorCode.PARSER_LAYOUT_CHANGED,
                "A página do portal fiscal mudou e não pôde ser interpretada.",
            )

        merchant_name = clean_text(merchant_name_node.get_text(" ", strip=True))
        merchant_cnpj_text = clean_text(text_blocks[0].get_text(" ", strip=True))
        cnpj_match = _CNPJ.search(merchant_cnpj_text)
        merchant_cnpj = re.sub(r"\D", "", cnpj_match.group(1)) if cnpj_match else ""
        merchant_address = clean_text(text_blocks[1].get_text(" ", strip=True))
        if not merchant_name or len(merchant_cnpj) != 14 or not merchant_address:
            raise _failure(
                ErrorCode.MISSING_REQUIRED_DATA,
                "A identificação fiscal do estabelecimento está incompleta.",
            )

        general_text = self._section_text(soup, "Informacoes gerais da Nota")
        access_text = self._section_text(soup, "Chave de acesso")
        key_match = _ACCESS_KEY.search(access_text)
        access_key = re.sub(r"\D", "", key_match.group(0)) if key_match else ""
        if len(access_key) != 44:
            raise _failure(
                ErrorCode.MISSING_REQUIRED_DATA,
                "A chave de acesso não foi encontrada na página fiscal.",
            )

        normalized_general = normalize_text(general_text)
        status = self._document_status(normalized_general)
        general = self._parse_general_info(normalized_general)
        if status == FiscalDocumentStatus.CANCELLED:
            return RawNfceDocument(
                merchant_name=merchant_name,
                merchant_cnpj=merchant_cnpj,
                merchant_address=merchant_address,
                access_key=access_key,
                status=status,
                items=[],
                **general,
            )
        if item_table is None or totals_node is None:
            raise _failure(
                ErrorCode.PARSER_LAYOUT_CHANGED,
                "A tabela de itens ou totais não foi encontrada na página fiscal.",
            )
        items = self._parse_items(item_table)
        totals = self._parse_totals(totals_node, items)
        return RawNfceDocument(
            merchant_name=merchant_name,
            merchant_cnpj=merchant_cnpj,
            merchant_address=merchant_address,
            access_key=access_key,
            status=status,
            items=items,
            **general,
            **totals,
        )

    def _section_text(self, soup: BeautifulSoup, title: str) -> str:
        expected = normalize_text(title)
        for section in soup.select("#infos .jqm-collapsible, #infos [data-role='collapsible']"):
            heading = section.select_one("h1, h2, h3, h4, .ui-collapsible-heading")
            if heading is None or normalize_text(heading.get_text(" ", strip=True)) != expected:
                continue
            content = section.select_one(".ui-collapsible-content")
            source = content if content is not None else section
            text = clean_text(source.get_text(" ", strip=True))
            heading_text = clean_text(heading.get_text(" ", strip=True))
            return clean_text(text.removeprefix(heading_text))
        raise _failure(
            ErrorCode.PARSER_LAYOUT_CHANGED,
            "Um bloco obrigatório da página fiscal não foi encontrado.",
        )

    def _document_status(self, general_text: str) -> FiscalDocumentStatus:
        if "cancelad" in general_text:
            return FiscalDocumentStatus.CANCELLED
        if (
            "emissao normal" in general_text
            or "autorizada" in general_text
            or "protocolo de autorizacao" in general_text
        ):
            return FiscalDocumentStatus.AUTHORIZED
        raise _failure(
            ErrorCode.MISSING_REQUIRED_DATA,
            "A situação fiscal da NFC-e não pôde ser confirmada.",
        )

    def _parse_general_info(self, text: str) -> dict[str, str | None]:
        number = _label(text, r"numero\s*:?\s*(\d+)")
        series = _label(text, r"serie\s*:?\s*(\d+)")
        issued_at = _label(text, r"emissao\s*:?\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})")
        protocol_match = re.search(
            r"protocolo de autorizacao\s*:?\s*(\d+)\s+"
            r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})",
            text,
        )
        if protocol_match is None:
            protocol_match = re.search(
                r"protocolo\s*:?\s*(\d+)\s+"
                r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})",
                text,
            )
        return {
            "document_number": number,
            "document_series": series,
            "issued_at": issued_at,
            "authorization_protocol": protocol_match.group(1) if protocol_match else None,
            "authorized_at": protocol_match.group(2) if protocol_match else None,
            "issue_type": _label(text, r"(emissao\s+(?:normal|cancelada|denegada))"),
            "environment": _label(text, r"ambiente\s*:?\s*([a-z ]+?)(?:\s+(?:versao|via|protocolo)|$)"),
            "xml_version": _label(text, r"versao xml\s*:?\s*([\d.]+)"),
            "xslt_version": _label(text, r"versao xslt\s*:?\s*([\d.]+)"),
        }

    def _parse_items(self, table: Tag) -> list[RawPurchaseItem]:
        items: list[RawPurchaseItem] = []
        for row in table.select("tr"):
            description = row.select_one("span.txtTit")
            if description is None:
                continue
            product_code = row.select_one("span.RCod")
            quantity_node = row.select_one("span.Rqtd")
            unit_node = row.select_one("span.RUN")
            unit_price_node = row.select_one("span.RvlUnit")
            total_node = row.select_one("span.valor")
            if any(node is None for node in (quantity_node, unit_node, unit_price_node, total_node)):
                raise _failure(
                    ErrorCode.PARSER_LAYOUT_CHANGED,
                    "Uma linha de item da NFC-e está incompleta.",
                )

            code_match = re.search(
                r"codigo\s*:?\s*([\w./-]+)",
                normalize_text(product_code.get_text(" ", strip=True)) if product_code else "",
            )
            quantity = _first_number(quantity_node.get_text(" ", strip=True))
            unit = _strip_label(unit_node.get_text(" ", strip=True), "un")
            unit_price = _first_number(unit_price_node.get_text(" ", strip=True))
            line_total = _last_number(total_node.get_text(" ", strip=True))
            item_description = clean_text(description.get_text(" ", strip=True))
            if not all((quantity, unit, unit_price, line_total, item_description)):
                raise _failure(
                    ErrorCode.MISSING_REQUIRED_DATA,
                    "Uma linha de item não contém descrição, quantidade, unidade ou valores válidos.",
                )
            items.append(
                RawPurchaseItem(
                    line_number=len(items) + 1,
                    store_product_code=code_match.group(1) if code_match else None,
                    description=item_description,
                    quantity=quantity,
                    commercial_unit=unit,
                    unit_price=unit_price,
                    line_total=line_total,
                )
            )
        if not items:
            raise _failure(ErrorCode.MISSING_REQUIRED_DATA, "A NFC-e não contém itens legíveis.")
        return items

    def _parse_totals(
        self,
        totals_node: Tag,
        items: list[RawPurchaseItem],
    ) -> dict[str, str | None]:
        fields: dict[str, str | None] = {
            "declared_line_count": None,
            "gross_items_amount": None,
            "total_discount": None,
            "payable_amount": None,
            "total_taxes": None,
        }
        for block in totals_node.find_all("div", recursive=False):
            label = block.find("label")
            amount = block.find("span", class_="totalNumb")
            if label is None or amount is None:
                continue
            normalized_label = normalize_text(label.get_text(" ", strip=True))
            amount_text = _last_number(amount.get_text(" ", strip=True))
            if amount_text is None:
                continue
            if normalized_label.startswith("qtd. total de itens") or normalized_label.startswith(
                "quantidade total de itens"
            ):
                fields["declared_line_count"] = amount_text
            elif normalized_label.startswith("valor a pagar"):
                fields["payable_amount"] = amount_text
            elif normalized_label.startswith("desconto") or normalized_label.startswith("descontos"):
                fields["total_discount"] = amount_text
            elif "tributos totais incidentes" in normalized_label:
                fields["total_taxes"] = amount_text
            elif "valor total" in normalized_label and "pagar" not in normalized_label:
                fields["gross_items_amount"] = amount_text

        if fields["total_discount"] is None:
            fields["total_discount"] = "0"
        if fields["gross_items_amount"] is None:
            fields["gross_items_amount"] = _sum_item_totals(items)
        required = ("declared_line_count", "gross_items_amount", "payable_amount")
        if any(fields[name] is None for name in required):
            raise _failure(
                ErrorCode.PARSER_LAYOUT_CHANGED,
                "Os totais obrigatórios da NFC-e não foram encontrados.",
            )
        return fields


def _label(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else None


def _first_number(value: str) -> str | None:
    match = _NUMBER.search(clean_text(value))
    return match.group(0) if match else None


def _last_number(value: str) -> str | None:
    matches = list(_NUMBER.finditer(clean_text(value)))
    return matches[-1].group(0) if matches else None


def _strip_label(value: str, label: str) -> str:
    cleaned = clean_text(value)
    return re.sub(rf"^{re.escape(label)}\s*:?\s*", "", cleaned, flags=re.IGNORECASE).strip()


def _sum_item_totals(items: list[RawPurchaseItem]) -> str:
    amounts: list[Decimal] = []
    for item in items:
        value = clean_text(item.line_total)
        normalized = value.replace(".", "").replace(",", ".") if "," in value else value
        try:
            amounts.append(Decimal(normalized))
        except InvalidOperation as exc:
            raise _failure(
                ErrorCode.PARSER_LAYOUT_CHANGED,
                "O valor bruto não aparece no resumo e não pôde ser calculado pelos itens.",
            ) from exc
    total = sum(amounts, Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(total, ".2f").replace(".", ",")


def _failure(code: ErrorCode, message: str) -> ProcessingFailure:
    return ProcessingFailure(code, message, ProcessingState.EXTRACTING_DATA)
