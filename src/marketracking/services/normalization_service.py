from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from marketracking.schemas.nfce import (
    ErrorCode,
    FiscalDocumentData,
    IssuedStoreDetailsData,
    ProcessingState,
    PurchaseData,
    PurchaseItemData,
    PurchaseTotalData,
    RawNfceDocument,
    StoreData,
)
from marketracking.services.consultation_url import (
    is_valid_access_key_check_digit,
)
from marketracking.services.errors import ProcessingFailure


SAO_PAULO = ZoneInfo("America/Sao_Paulo")
CENT = Decimal("0.01")
TOLERANCE = Decimal("0.01")


def normalize_nfce(raw: RawNfceDocument, url_access_key: str) -> PurchaseData:
    access_key = validate_parsed_access_key(raw.access_key, url_access_key)

    cnpj = _digits(raw.merchant_cnpj)
    if len(cnpj) != 14:
        raise _failure(ErrorCode.MISSING_REQUIRED_DATA, "O CNPJ do estabelecimento é inválido.")

    issued_at = _parse_datetime(raw.issued_at, "data de emissão")
    authorized_at = _parse_datetime(raw.authorized_at, "data de autorização")
    if not raw.document_number or not raw.document_series or not raw.authorization_protocol:
        raise _failure(
            ErrorCode.MISSING_REQUIRED_DATA,
            "Os dados de identificação e autorização da NFC-e estão incompletos.",
        )

    items = [
        PurchaseItemData(
            line_number=item.line_number,
            store_product_code=item.store_product_code,
            description=item.description,
            quantity=_parse_decimal(item.quantity, "quantidade"),
            commercial_unit=_normalize_unit(item.commercial_unit),
            unit_price=_parse_decimal(item.unit_price, "preço unitário"),
            line_total=_parse_money(item.line_total, "total da linha"),
        )
        for item in raw.items
    ]
    declared_line_count = _parse_integer(raw.declared_line_count, "quantidade de itens")
    gross_items_amount = _parse_money(raw.gross_items_amount, "valor bruto")
    total_discount = _parse_money(raw.total_discount or "0", "desconto")
    payable_amount = _parse_money(raw.payable_amount, "valor final")
    total_taxes = (
        _parse_money(raw.total_taxes, "tributos") if raw.total_taxes is not None else None
    )


    if declared_line_count != len(items):
        raise _failure(
            ErrorCode.MONETARY_INCONSISTENCY,
            "A quantidade de linhas declarada diverge dos itens extraídos.",
        )
    for item in items:
        expected = (item.quantity * item.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
        if abs(expected - item.line_total) > TOLERANCE:
            raise _monetary_failure("A quantidade e o preço unitário divergem do total da linha.")

    item_sum = sum((item.line_total for item in items), Decimal("0.00"))
    if abs(item_sum - gross_items_amount) > TOLERANCE:
        raise _monetary_failure("A soma dos itens diverge do valor bruto declarado.")
    if abs(gross_items_amount - total_discount - payable_amount) > TOLERANCE:
        raise _monetary_failure("O valor bruto menos o desconto diverge do valor final declarado.")

    fiscal_document = FiscalDocumentData(
        access_key=access_key,
        number=raw.document_number,
        series=raw.document_series,
        issued_at=issued_at,
        authorization_protocol=raw.authorization_protocol,
        authorized_at=authorized_at,
        status=raw.status,
        environment=_clean_optional(raw.environment),
        xml_version=_clean_optional(raw.xml_version),
        xslt_version=_clean_optional(raw.xslt_version),
    )
    store = StoreData(
        cnpj=cnpj,
        name=raw.merchant_name,
        address=raw.merchant_address,
        details_as_of=issued_at,
    )
    return PurchaseData(
        fiscal_document=fiscal_document,
        store=store,
        issued_store_details=IssuedStoreDetailsData(
            name=raw.merchant_name,
            address=raw.merchant_address,
        ),
        items=items,
        total=PurchaseTotalData(
            declared_line_count=declared_line_count,
            gross_items_amount=gross_items_amount,
            total_discount=total_discount,
            payable_amount=payable_amount,
            total_taxes=total_taxes,
        ),
    )


def validate_parsed_access_key(value: str, url_access_key: str) -> str:
    access_key = _digits(value)
    if len(access_key) != 44 or not is_valid_access_key_check_digit(access_key):
        raise _failure(ErrorCode.INVALID_ACCESS_KEY, "A chave exibida pelo portal é inválida.")
    if not access_key.startswith("35"):
        raise _failure(
            ErrorCode.UNSUPPORTED_STATE,
            "Neste momento, são aceitas somente NFC-e emitidas em São Paulo.",
        )
    if access_key != url_access_key:
        raise _failure(
            ErrorCode.ACCESS_KEY_MISMATCH,
            "A chave da página fiscal diverge da chave da URL de consulta.",
        )
    return access_key


def _parse_datetime(value: str | None, field: str) -> datetime:
    if not value:
        raise _failure(ErrorCode.MISSING_REQUIRED_DATA, f"A {field} da NFC-e não foi encontrada.")
    try:
        parsed = datetime.strptime(value, "%d/%m/%Y %H:%M:%S")
    except ValueError as exc:
        raise _failure(
            ErrorCode.MISSING_REQUIRED_DATA,
            f"A {field} da NFC-e é inválida.",
            cause=type(exc).__name__,
        ) from exc
    return parsed.replace(tzinfo=SAO_PAULO)


def _parse_decimal(value: str | None, field: str) -> Decimal:
    if value is None:
        raise _failure(ErrorCode.MISSING_REQUIRED_DATA, f"O campo {field} está ausente.")
    cleaned = value.strip().replace("R$", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        raise _failure(ErrorCode.MISSING_REQUIRED_DATA, f"O campo {field} é inválido.")
    try:
        value_decimal = Decimal(cleaned)
    except InvalidOperation as exc:
        raise _failure(
            ErrorCode.MISSING_REQUIRED_DATA,
            f"O campo {field} é inválido.",
            cause=type(exc).__name__,
        ) from exc
    return value_decimal


def _parse_money(value: str | None, field: str) -> Decimal:
    result = _parse_decimal(value, field)
    return result.quantize(CENT, rounding=ROUND_HALF_UP)


def _parse_integer(value: str | None, field: str) -> int:
    if value is None or not value.strip().isdigit():
        raise _failure(ErrorCode.MISSING_REQUIRED_DATA, f"O campo {field} é inválido.")
    return int(value.strip())


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _normalize_unit(value: str) -> str:
    normalized = " ".join(value.upper().split())
    if not normalized or len(normalized) > 32:
        raise _failure(ErrorCode.MISSING_REQUIRED_DATA, "A unidade comercial do item é inválida.")
    return normalized


def _clean_optional(value: str | None) -> str | None:
    cleaned = " ".join(value.split()) if value else ""
    return cleaned or None


def _failure(
    code: ErrorCode,
    message: str,
    *,
    cause: str | None = None,
) -> ProcessingFailure:
    return ProcessingFailure(
        code=code,
        message=message,
        stage=ProcessingState.VALIDATING_DATA,
        technical_cause=cause,
    )


def _monetary_failure(message: str) -> ProcessingFailure:
    return _failure(ErrorCode.MONETARY_INCONSISTENCY, message)
