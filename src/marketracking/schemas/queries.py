from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from marketracking.schemas.nfce import (
    CaptureMethod,
    FiscalDocumentStatus,
    ProcessingOutcome,
    ProcessingState,
)


class PurchaseStatusFilter(StrEnum):
    AUTHORIZED = "authorized"
    CANCELLED = "cancelled"
    ALL = "all"


class PurchaseSort(StrEnum):
    ISSUED_AT_DESC = "issued_at_desc"
    ISSUED_AT_ASC = "issued_at_asc"
    PAYABLE_AMOUNT_DESC = "payable_amount_desc"
    PAYABLE_AMOUNT_ASC = "payable_amount_asc"


class ItemSort(StrEnum):
    ISSUED_AT_DESC = "issued_at_desc"
    ISSUED_AT_ASC = "issued_at_asc"
    UNIT_PRICE_ASC = "unit_price_asc"
    UNIT_PRICE_DESC = "unit_price_desc"


class StoreSort(StrEnum):
    NAME_ASC = "name_asc"
    PAYABLE_AMOUNT_DESC = "payable_amount_desc"
    PURCHASE_COUNT_DESC = "purchase_count_desc"


class ProcessingSort(StrEnum):
    CREATED_AT_DESC = "created_at_desc"
    CREATED_AT_ASC = "created_at_asc"


T = TypeVar("T")


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class Page(BaseModel, Generic[T]):
    data: list[T]
    pagination: Pagination


class PurchaseTotalRead(BaseModel):
    declared_line_count: int
    gross_items_amount: Decimal = Field(description="BRL gross amount from source item lines.")
    total_discount: Decimal = Field(description="Purchase-wide discount in BRL; not allocated to lines.")
    payable_amount: Decimal = Field(description="Final purchase amount in BRL.")
    total_taxes: Decimal | None = Field(
        description="Known tax total in BRL, or null when the fiscal document has no tax value."
    )


class FiscalDocumentRead(BaseModel):
    fiscal_document_id: UUID
    access_key: str
    number: str
    series: str
    issued_at: datetime
    authorization_protocol: str
    authorized_at: datetime
    status: FiscalDocumentStatus
    environment: str | None
    xml_version: str | None
    xslt_version: str | None


class StoreRead(BaseModel):
    id: UUID
    cnpj: str
    name: str
    address: str
    details_as_of: datetime


class PurchaseItemRead(BaseModel):
    item_id: UUID
    line_number: int
    store_product_code: str | None
    description: str
    quantity: Decimal = Field(description="Commercial quantity with six decimal places.")
    commercial_unit: str
    unit_price: Decimal = Field(description="Declared commercial unit price with six decimals, BRL.")
    line_total: Decimal = Field(description="Gross source line total in BRL; no purchase discount allocation.")


class PurchaseListItem(BaseModel):
    purchase_id: UUID
    fiscal_document_id: UUID
    issued_at: datetime
    status: FiscalDocumentStatus
    store_cnpj: str
    issued_store_name: str
    item_line_count: int
    gross_items_amount: Decimal
    total_discount: Decimal
    payable_amount: Decimal
    total_taxes: Decimal | None


class PurchaseDetail(BaseModel):
    purchase_id: UUID
    fiscal_document: FiscalDocumentRead
    store: StoreRead
    issued_store_details: dict[str, str]
    items: list[PurchaseItemRead]
    total: PurchaseTotalRead


class PurchaseItemListItem(BaseModel):
    item_id: UUID
    purchase_id: UUID
    issued_at: datetime
    status: FiscalDocumentStatus
    store_cnpj: str
    issued_store_name: str
    line_number: int
    store_product_code: str | None
    description: str
    quantity: Decimal
    commercial_unit: str
    unit_price: Decimal
    line_total: Decimal


class StoreSummary(BaseModel):
    purchase_count: int
    payable_amount: Decimal
    last_purchase_at: datetime | None


class StoreListItem(BaseModel):
    id: UUID
    cnpj: str
    name: str
    address: str
    details_as_of: datetime
    summary: StoreSummary


class StoreDetail(StoreListItem):
    pass


class ProcessingErrorSummary(BaseModel):
    code: str | None
    message: str | None


class ProcessingListItem(BaseModel):
    processamento_id: UUID
    purchase_id: UUID | None
    capture_method: CaptureMethod
    state: ProcessingState
    result: ProcessingOutcome | None
    created_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error: ProcessingErrorSummary | None


class DatePeriodMeta(BaseModel):
    date_from: date = Field(description="Effective inclusive local start date.")
    date_to: date = Field(description="Effective inclusive local end date.")
    timezone: str = Field(default="America/Sao_Paulo", description="Timezone used for date boundaries and buckets.")
    currency: str = Field(default="BRL", description="Currency for monetary values.")
    store_cnpj: str | None = None
    filters: dict[str, str | None] = Field(
        default_factory=dict, description="Effective filters used to produce this aggregate."
    )


class ItemIdentityKind(StrEnum):
    STORE_CODE = "store_code"
    SOURCE_DESCRIPTION = "source_description"


class ItemIdentity(BaseModel):
    kind: ItemIdentityKind = Field(
        description="store_code uses the store-local code; source_description uses an exact uncoded description."
    )
    store_cnpj: str
    store_product_code: str | None = None
    description: str | None = None
    commercial_unit: str


class PriceObservation(BaseModel):
    purchase_id: UUID
    issued_at: datetime
    description: str
    observed_unit_price: Decimal = Field(description="Quantity-weighted unit price with six decimals, BRL.")
    quantity: Decimal = Field(description="Total quantity for the commercial unit, with six decimals.")
    item_line_count: int


class PriceBucket(BaseModel):
    bucket_start: date
    bucket_end: date
    observed_unit_price: Decimal | None = Field(
        description="Quantity-weighted unit price with six decimals, or null when no purchase was observed."
    )
    quantity: Decimal = Field(description="Total quantity in the requested commercial unit.")
    observation_count: int


class PriceSummary(BaseModel):
    observation_count: int
    quantity: Decimal = Field(description="Total quantity over the effective period and identity.")
    minimum_unit_price: Decimal | None = Field(description="Minimum per-purchase observed price, or null without data.")
    maximum_unit_price: Decimal | None = Field(description="Maximum per-purchase observed price, or null without data.")
    weighted_average_unit_price: Decimal | None = Field(description="Quantity-weighted price over original lines.")
    last_unit_price: Decimal | None = Field(description="Latest per-purchase observed price, or null without data.")
    last_issued_at: datetime | None
    delta: Decimal | None
    delta_percent: Decimal | None


class PriceReport(BaseModel):
    meta: DatePeriodMeta
    identity: ItemIdentity
    summary: PriceSummary
    data: list[PriceObservation] | list[PriceBucket]
    pagination: Pagination | None = None


class ConfiguredModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
