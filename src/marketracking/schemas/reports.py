from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from marketracking.schemas.queries import DatePeriodMeta, ItemIdentity


class SpendingGroup(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    STORE = "store"


class PriceBucketGroup(StrEnum):
    PURCHASE = "purchase"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class SpendingSummary(BaseModel):
    purchase_count: int
    gross_items_amount: Decimal
    total_discount: Decimal
    payable_amount: Decimal
    average_ticket: Decimal
    item_line_count: int
    store_count: int
    known_taxes_amount: Decimal | None = Field(
        description="Sum of known tax values in BRL; null when none of the purchases has tax data."
    )
    purchases_with_tax_info: int


class SpendingBucket(BaseModel):
    bucket_start: date
    bucket_end: date
    purchase_count: int
    gross_items_amount: Decimal
    total_discount: Decimal
    payable_amount: Decimal
    average_ticket: Decimal


class StoreSpending(BaseModel):
    store_cnpj: str
    store_name: str
    last_purchase_at: datetime
    purchase_count: int
    payable_amount: Decimal
    share_percent: Decimal | None = Field(
        description="Share of total payable amount on a 0–100 scale, or null when the total is zero."
    )


class SpendingOthers(BaseModel):
    store_count: int
    purchase_count: int
    payable_amount: Decimal
    share_percent: Decimal | None = Field(
        description="Combined share of stores outside the result limit, or null when total spending is zero."
    )


class SpendingReport(BaseModel):
    meta: DatePeriodMeta
    group_by: SpendingGroup
    summary: SpendingSummary
    data: list[SpendingBucket] | list[StoreSpending]
    others: SpendingOthers | None = None


class PriceChangeDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"
    ALL = "all"


class PricePoint(BaseModel):
    purchase_id: UUID
    issued_at: datetime
    observed_unit_price: Decimal


class PriceChangeItem(BaseModel):
    identity: ItemIdentity
    description: str
    current: PricePoint
    previous: PricePoint
    delta: Decimal
    delta_percent: Decimal


class PriceChangeSummary(BaseModel):
    no_previous_count: int
    unchanged_count: int
    zero_baseline_count: int
    changed_group_count: int


class PriceChangesReport(BaseModel):
    meta: DatePeriodMeta
    summary: PriceChangeSummary
    data: list[PriceChangeItem]
