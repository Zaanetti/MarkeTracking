from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from marketracking.schemas.queries import DatePeriodMeta, ItemIdentity
from marketracking.schemas.reports import PriceChangeItem, PriceChangeSummary


class OverviewMeta(DatePeriodMeta):
    previous_date_from: date
    previous_date_to: date


class OverviewKpis(BaseModel):
    purchase_count: int
    payable_amount: Decimal
    gross_items_amount: Decimal
    total_discount: Decimal
    average_ticket: Decimal
    item_line_count: int
    store_count: int
    known_taxes_amount: Decimal | None = Field(
        description="Sum of known taxes in BRL; null when no purchase has tax data."
    )
    purchases_with_tax_info: int
    tax_info_coverage_percent: Decimal | None = Field(
        description="Purchases with known tax data divided by purchases, on a 0–100 scale; null for zero purchases."
    )
    items_with_local_code_count: int
    local_code_coverage_percent: Decimal | None = Field(
        description="Lines with a store-local product code divided by item lines, on a 0–100 scale; null for zero lines."
    )


class MetricComparison(BaseModel):
    delta: int | Decimal
    delta_percent: Decimal | None


class OverviewDashboard(BaseModel):
    meta: OverviewMeta
    current: OverviewKpis
    previous: OverviewKpis
    comparison: dict[str, MetricComparison]


class ItemMetric(StrEnum):
    GROSS_LINE_AMOUNT = "gross_line_amount"
    PURCHASE_COUNT = "purchase_count"
    QUANTITY = "quantity"


class DashboardItem(BaseModel):
    identity: ItemIdentity
    description: str
    description_variants_count: int
    item_line_count: int
    purchase_count: int
    quantity: Decimal = Field(description="Sum of source quantities in commercial_unit; never converted across units.")
    commercial_unit: str
    gross_line_amount: Decimal = Field(
        description="Sum of source line totals in BRL before purchase-wide discounts."
    )
    first_issued_at: datetime
    last_issued_at: datetime
    last_unit_price: Decimal | None = Field(
        description="Latest per-purchase quantity-weighted unit price, or null without observations."
    )


class QuantityByUnit(BaseModel):
    commercial_unit: str
    quantity: Decimal


class ItemsSummary(BaseModel):
    group_count: int
    item_line_count: int
    gross_line_amount: Decimal
    quantity_by_unit: list[QuantityByUnit]


class ItemsDashboard(BaseModel):
    meta: DatePeriodMeta
    metric: ItemMetric
    summary: ItemsSummary
    data: list[DashboardItem]


class PriceChangesDashboard(BaseModel):
    meta: DatePeriodMeta
    summary: PriceChangeSummary
    data: list[PriceChangeItem]


class ProcessingBucketGroup(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class ProcessingOutcomeCount(BaseModel):
    result: str | None
    count: int


class ProcessingErrorCount(BaseModel):
    error_code: str
    count: int


class ProcessingDashboardSummary(BaseModel):
    processing_count: int
    successful_count: int
    failed_count: int
    rejected_count: int
    in_progress_count: int
    existing_return_count: int
    success_rate_percent: Decimal | None
    duration_p50_ms: Decimal | None = Field(description="PostgreSQL percentile_cont result in milliseconds.")
    duration_p95_ms: Decimal | None = Field(description="PostgreSQL percentile_cont result in milliseconds.")


class ProcessingDashboardBucket(BaseModel):
    bucket_start: date
    bucket_end: date
    processing_count: int
    successful_count: int
    failed_count: int
    rejected_count: int
    in_progress_count: int
    success_rate_percent: Decimal | None


class ProcessingsDashboard(BaseModel):
    meta: DatePeriodMeta
    bucket: ProcessingBucketGroup
    summary: ProcessingDashboardSummary
    outcomes: list[ProcessingOutcomeCount]
    error_distribution: list[ProcessingErrorCount]
    data: list[ProcessingDashboardBucket]
