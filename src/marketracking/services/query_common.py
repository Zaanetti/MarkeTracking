from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from marketracking.services.consultation_url import is_valid_access_key_check_digit


SAO_PAULO = ZoneInfo("America/Sao_Paulo")
TIMEZONE_NAME = "America/Sao_Paulo"
CENT = Decimal("0.01")
SIX_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class Period:
    date_from: date
    date_to: date

    @property
    def start(self) -> datetime:
        return datetime.combine(self.date_from, time.min, tzinfo=SAO_PAULO)

    @property
    def end_exclusive(self) -> datetime:
        return datetime.combine(self.date_to + timedelta(days=1), time.min, tzinfo=SAO_PAULO)


@dataclass(frozen=True)
class DateBounds:
    date_from: date | None
    date_to: date | None

    @property
    def start(self) -> datetime | None:
        return (
            datetime.combine(self.date_from, time.min, tzinfo=SAO_PAULO)
            if self.date_from is not None
            else None
        )

    @property
    def end_exclusive(self) -> datetime | None:
        return (
            datetime.combine(self.date_to + timedelta(days=1), time.min, tzinfo=SAO_PAULO)
            if self.date_to is not None
            else None
        )


def resolve_date_bounds(date_from: date | None, date_to: date | None) -> DateBounds | None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from deve ser menor ou igual a date_to.")
    if date_from is None and date_to is None:
        return None
    return DateBounds(date_from=date_from, date_to=date_to)


def resolve_period(
    date_from: date | None,
    date_to: date | None,
    *,
    reporting: bool,
) -> Period | None:
    today = datetime.now(SAO_PAULO).date()
    if date_from is None and date_to is None:
        date_to = today
        date_from = today - timedelta(days=29)
    elif date_from is None:
        assert date_to is not None
        date_from = date_to - timedelta(days=29)
    elif date_to is None:
        date_to = today
    if date_from > date_to:
        raise ValueError("date_from deve ser menor ou igual a date_to.")
    period = Period(date_from, date_to)
    if reporting and (date_to - date_from).days + 1 > 366:
        raise ValueError("O período máximo para relatórios e dashboards é de 366 dias.")
    return period


def validate_cnpj(value: str) -> str:
    if len(value) != 14 or not value.isascii() or not value.isdigit():
        raise ValueError("CNPJ deve conter exatamente 14 dígitos.")
    return value


def validate_access_key(value: str) -> str:
    if len(value) != 44 or not value.isascii() or not value.isdigit():
        raise ValueError("A chave fiscal deve conter exatamente 44 dígitos.")
    if not is_valid_access_key_check_digit(value) or not value.startswith("35"):
        raise ValueError("A chave fiscal é inválida.")
    return value


def escaped_like(value: str) -> str:
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= 120:
        raise ValueError("q deve conter de 1 a 120 caracteres.")
    return f"%{cleaned.replace(chr(92), chr(92) * 2).replace('%', chr(92) + '%').replace('_', chr(92) + '_')}%"


def money(value: Decimal | int | None) -> Decimal:
    return (Decimal(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def precise(value: Decimal | int | None) -> Decimal:
    return (Decimal(value or 0)).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


def percent(value: Decimal | int | None) -> Decimal:
    return (Decimal(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return percent((current - previous) / previous * Decimal(100))


def round_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


def begin_read_snapshot(db: Session) -> None:
    """Make multi-query GET calculations observe one PostgreSQL snapshot."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


def make_buckets(period: Period, group_by: str) -> list[tuple[date, date]]:
    buckets: list[tuple[date, date]] = []
    cursor = period.date_from
    while cursor <= period.date_to:
        if group_by == "day":
            start = end = cursor
        elif group_by == "week":
            start = cursor - timedelta(days=cursor.weekday())
            end = start + timedelta(days=6)
        else:
            start = cursor.replace(day=1)
            if cursor.month == 12:
                end = date(cursor.year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(cursor.year, cursor.month + 1, 1) - timedelta(days=1)
        clipped = (max(start, period.date_from), min(end, period.date_to))
        if not buckets or buckets[-1] != clipped:
            buckets.append(clipped)
        cursor = end + timedelta(days=1)
    return buckets


def clipped_bucket_start(bucket_key: date, period: Period, group_by: str) -> date:
    """Map date_trunc's calendar start to the first requested date in that bucket."""
    if group_by == "day":
        return bucket_key
    if group_by == "week":
        bucket_end = bucket_key + timedelta(days=6)
    else:
        if bucket_key.month == 12:
            bucket_end = date(bucket_key.year + 1, 1, 1) - timedelta(days=1)
        else:
            bucket_end = date(bucket_key.year, bucket_key.month + 1, 1) - timedelta(days=1)
    return max(bucket_key, period.date_from) if bucket_end >= period.date_from else bucket_key


def previous_period(period: Period) -> Period:
    days = (period.date_to - period.date_from).days + 1
    previous_to = period.date_from - timedelta(days=1)
    return Period(previous_to - timedelta(days=days - 1), previous_to)
