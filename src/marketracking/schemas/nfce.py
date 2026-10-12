from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CaptureMethod(StrEnum):
    IMAGE = "image"
    URL = "url"
    INVALID = "invalid"


class ProcessingState(StrEnum):
    RECEIVED = "RECEBIDO"
    VALIDATING_INPUT = "VALIDANDO_ENTRADA"
    PROCESSING_IMAGE = "PROCESSANDO_IMAGEM"
    DECODING_QR = "DECODIFICANDO_QRCODE"
    VALIDATING_URL = "VALIDANDO_URL"
    FETCHING_PORTAL = "CONSULTANDO_PORTAL"
    VALIDATING_RESPONSE = "VALIDANDO_RESPOSTA"
    EXTRACTING_DATA = "EXTRAINDO_DADOS"
    VALIDATING_DATA = "VALIDANDO_DADOS"
    CHECKING_DUPLICATE = "VERIFICANDO_DUPLICIDADE"
    PERSISTING = "PERSISTINDO"
    COMPLETED = "CONCLUIDO"
    REJECTED = "REJEITADO"
    FAILED = "FALHA"


class ProcessingOutcome(StrEnum):
    CREATED = "created"
    EXISTING = "existing"
    CANCELLED_EXISTING = "cancelled_existing"
    REJECTED = "rejected"
    FAILED = "failed"


class FiscalDocumentStatus(StrEnum):
    AUTHORIZED = "authorized"
    CANCELLED = "cancelled"


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_IMAGE = "invalid_image"
    QR_NOT_FOUND = "qr_not_found"
    QR_UNDECODABLE = "qr_undecodable"
    MULTIPLE_QR_CODES = "multiple_qr_codes"
    INVALID_CONSULTATION_URL = "invalid_consultation_url"
    UNSUPPORTED_STATE = "unsupported_state"
    INVALID_ACCESS_KEY = "invalid_access_key"
    ACCESS_KEY_MISMATCH = "access_key_mismatch"
    DOCUMENT_NOT_FOUND = "document_not_found"
    DOCUMENT_CANCELLED = "document_cancelled"
    PORTAL_BLOCKED = "portal_blocked"
    PORTAL_UNAVAILABLE = "portal_unavailable"
    UNEXPECTED_PORTAL_RESPONSE = "unexpected_portal_response"
    PARSER_LAYOUT_CHANGED = "parser_layout_changed"
    MISSING_REQUIRED_DATA = "missing_required_data"
    MONETARY_INCONSISTENCY = "monetary_inconsistency"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"
    INTERNAL_ERROR = "internal_error"


class ProcessingError(BaseModel):
    code: ErrorCode
    message: str
    stage: ProcessingState
    transient: bool = False
    retryable: bool = False
    technical_cause: str | None = None


class StoreData(BaseModel):
    model_config = ConfigDict(frozen=True)

    cnpj: str = Field(pattern=r"^\d{14}$")
    name: str = Field(min_length=1, max_length=255)
    address: str = Field(min_length=1, max_length=512)
    details_as_of: datetime

    @field_validator("details_as_of")
    @classmethod
    def details_timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)


class IssuedStoreDetailsData(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=255)
    address: str = Field(min_length=1, max_length=512)


class FiscalDocumentData(BaseModel):
    model_config = ConfigDict(frozen=True)

    fiscal_document_id: UUID | None = None
    access_key: str = Field(pattern=r"^\d{44}$")
    number: str = Field(min_length=1, max_length=32)
    series: str = Field(min_length=1, max_length=16)
    issued_at: datetime
    authorization_protocol: str = Field(min_length=1, max_length=64)
    authorized_at: datetime
    status: FiscalDocumentStatus
    environment: str | None = Field(default=None, max_length=32)
    xml_version: str | None = Field(default=None, max_length=32)
    xslt_version: str | None = Field(default=None, max_length=32)

    @field_validator("issued_at", "authorized_at")
    @classmethod
    def fiscal_timestamps_must_be_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)


class PurchaseItemData(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: UUID | None = None
    line_number: int = Field(ge=1)
    store_product_code: str | None = Field(default=None, max_length=120)
    description: str = Field(min_length=1, max_length=512)
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=6)
    commercial_unit: str = Field(min_length=1, max_length=32)
    unit_price: Decimal = Field(ge=0, max_digits=16, decimal_places=6)
    line_total: Decimal = Field(ge=0, max_digits=16, decimal_places=2)


class PurchaseTotalData(BaseModel):
    model_config = ConfigDict(frozen=True)

    declared_line_count: int = Field(ge=1)
    gross_items_amount: Decimal = Field(ge=0, max_digits=16, decimal_places=2)
    total_discount: Decimal = Field(ge=0, max_digits=16, decimal_places=2)
    payable_amount: Decimal = Field(ge=0, max_digits=16, decimal_places=2)
    total_taxes: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)


class PurchaseData(BaseModel):
    model_config = ConfigDict(frozen=True)

    purchase_id: UUID | None = None
    fiscal_document: FiscalDocumentData
    store: StoreData
    issued_store_details: IssuedStoreDetailsData
    items: list[PurchaseItemData] = Field(min_length=1)
    total: PurchaseTotalData


class ProcessingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    processamento_id: UUID
    state: ProcessingState
    result: ProcessingOutcome
    already_processed: bool
    purchase: PurchaseData | None = None
    error: ProcessingError | None = None


class ProcessingStepData(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: ProcessingState
    attempt: int = Field(ge=1)
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: ErrorCode | None = None


class ProcessingTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    processamento_id: UUID
    purchase_id: UUID | None = None
    capture_method: CaptureMethod
    state: ProcessingState
    result: ProcessingOutcome | None = None
    created_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    input_metadata: dict[str, Any]
    steps: list[ProcessingStepData]
    error: ProcessingError | None = None


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


class RawPurchaseItem(BaseModel):
    line_number: int
    store_product_code: str | None = None
    description: str
    quantity: str
    commercial_unit: str
    unit_price: str
    line_total: str


class RawNfceDocument(BaseModel):
    merchant_name: str
    merchant_cnpj: str
    merchant_address: str
    access_key: str
    document_number: str | None = None
    document_series: str | None = None
    issued_at: str | None = None
    authorization_protocol: str | None = None
    authorized_at: str | None = None
    issue_type: str | None = None
    environment: str | None = None
    xml_version: str | None = None
    xslt_version: str | None = None
    status: FiscalDocumentStatus
    items: list[RawPurchaseItem]
    declared_line_count: str | None = None
    gross_items_amount: str | None = None
    total_discount: str | None = None
    payable_amount: str | None = None
    total_taxes: str | None = None
