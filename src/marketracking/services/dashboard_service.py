from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Numeric, and_, case, func, select
from sqlalchemy.orm import Session

from marketracking.db.models import FiscalDocument, Processing, Purchase, PurchaseItem, PurchaseTotal, Store
from marketracking.schemas.nfce import FiscalDocumentStatus, ProcessingOutcome, ProcessingState
from marketracking.services.query_common import (
    Period,
    TIMEZONE_NAME,
    clipped_bucket_start,
    escaped_like,
    make_buckets,
    money,
    percent,
    percent_change,
    precise,
    previous_period,
    validate_cnpj,
)


SUCCESS_OUTCOMES = (
    ProcessingOutcome.CREATED,
    ProcessingOutcome.EXISTING,
    ProcessingOutcome.CANCELLED_EXISTING,
)


def _financial_kpis(db: Session, period: Period, store_cnpj: str | None) -> dict:
    clauses = [
        FiscalDocument.issued_at >= period.start,
        FiscalDocument.issued_at < period.end_exclusive,
        FiscalDocument.status == FiscalDocumentStatus.AUTHORIZED,
        Purchase.status == FiscalDocumentStatus.AUTHORIZED,
    ]
    if store_cnpj:
        clauses.append(Store.cnpj == validate_cnpj(store_cnpj))
    row = db.execute(
        select(
            func.count(Purchase.id),
            func.coalesce(func.sum(PurchaseTotal.payable_amount), 0),
            func.coalesce(func.sum(PurchaseTotal.gross_items_amount), 0),
            func.coalesce(func.sum(PurchaseTotal.total_discount), 0),
            func.coalesce(func.sum(PurchaseTotal.declared_line_count), 0),
            func.count(func.distinct(Store.id)),
            func.sum(PurchaseTotal.total_taxes),
            func.count(PurchaseTotal.total_taxes),
        )
        .select_from(Purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .join(Purchase.total)
        .where(*clauses)
    ).one()
    count = int(row[0] or 0)
    payable = money(row[1])
    line_count = int(row[4] or 0)
    code_clauses = [
        FiscalDocument.issued_at >= period.start,
        FiscalDocument.issued_at < period.end_exclusive,
        FiscalDocument.status == FiscalDocumentStatus.AUTHORIZED,
        Purchase.status == FiscalDocumentStatus.AUTHORIZED,
    ]
    if store_cnpj:
        code_clauses.append(Store.cnpj == validate_cnpj(store_cnpj))
    coded_lines = db.scalar(
        select(func.count(PurchaseItem.id))
        .select_from(PurchaseItem)
        .join(PurchaseItem.purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .where(*code_clauses, PurchaseItem.store_product_code.is_not(None))
    ) or 0
    return {
        "purchase_count": count,
        "payable_amount": payable,
        "gross_items_amount": money(row[2]),
        "total_discount": money(row[3]),
        "average_ticket": money(payable / count) if count else money(0),
        "item_line_count": line_count,
        "store_count": int(row[5] or 0),
        "known_taxes_amount": money(row[6]) if row[7] else None,
        "purchases_with_tax_info": int(row[7] or 0),
        "tax_info_coverage_percent": percent(Decimal(int(row[7] or 0)) / count * 100)
        if count
        else None,
        "items_with_local_code_count": int(coded_lines),
        "local_code_coverage_percent": percent(Decimal(coded_lines) / line_count * 100)
        if line_count
        else None,
    }


def overview(db: Session, period: Period, store_cnpj: str | None) -> dict:
    previous = previous_period(period)
    current_values = _financial_kpis(db, period, store_cnpj)
    previous_values = _financial_kpis(db, previous, store_cnpj)
    comparison = {}
    for metric in ("purchase_count", "payable_amount", "average_ticket"):
        current_value = current_values[metric]
        previous_value = previous_values[metric]
        delta = current_value - previous_value
        comparison[metric] = {
            "delta": delta if metric == "purchase_count" else money(delta),
            "delta_percent": percent_change(Decimal(current_value), Decimal(previous_value)),
        }
    return {
        "meta": {
            "date_from": period.date_from,
            "date_to": period.date_to,
            "previous_date_from": previous.date_from,
            "previous_date_to": previous.date_to,
            "timezone": TIMEZONE_NAME,
            "currency": "BRL",
            "store_cnpj": store_cnpj,
            "filters": {"store_cnpj": store_cnpj},
        },
        "current": current_values,
        "previous": previous_values,
        "comparison": comparison,
    }


def items_dashboard(
    db: Session,
    *,
    period: Period,
    store_cnpj: str | None,
    q: str | None,
    commercial_unit: str | None,
    metric: str,
    limit: int,
) -> dict:
    code = PurchaseItem.store_product_code
    item_desc = PurchaseItem.description
    identity_description = case((code.is_not(None), None), else_=item_desc).label(
        "identity_description"
    )
    clauses = [
        FiscalDocument.issued_at >= period.start,
        FiscalDocument.issued_at < period.end_exclusive,
        FiscalDocument.status == FiscalDocumentStatus.AUTHORIZED,
        Purchase.status == FiscalDocumentStatus.AUTHORIZED,
    ]
    if store_cnpj:
        clauses.append(Store.cnpj == validate_cnpj(store_cnpj))
    if commercial_unit:
        clauses.append(PurchaseItem.commercial_unit == commercial_unit)
    dimensions = (Store.cnpj, code, identity_description, PurchaseItem.commercial_unit)
    grouped = (
        select(
            Store.cnpj.label("store_cnpj"),
            code.label("store_product_code"),
            identity_description,
            PurchaseItem.commercial_unit.label("commercial_unit"),
            func.count(PurchaseItem.id).label("item_line_count"),
            func.count(func.distinct(Purchase.id)).label("purchase_count"),
            func.sum(PurchaseItem.quantity).label("quantity"),
            func.sum(PurchaseItem.line_total).label("gross_line_amount"),
            func.min(FiscalDocument.issued_at).label("first_issued_at"),
            func.max(FiscalDocument.issued_at).label("last_issued_at"),
            func.count(func.distinct(PurchaseItem.description)).label("description_variants_count"),
        )
        .select_from(PurchaseItem)
        .join(PurchaseItem.purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .where(*clauses)
        .group_by(*dimensions)
        .cte("item_groups")
    )
    latest_ranked = (
        select(
            Store.cnpj.label("store_cnpj"),
            code.label("store_product_code"),
            identity_description,
            PurchaseItem.commercial_unit.label("commercial_unit"),
            PurchaseItem.description.label("description"),
            func.row_number()
            .over(
                partition_by=dimensions,
                order_by=(
                    FiscalDocument.issued_at.desc(),
                    Purchase.id.desc(),
                    PurchaseItem.line_number.desc(),
                    PurchaseItem.id.desc(),
                ),
            )
            .label("rank"),
        )
        .select_from(PurchaseItem)
        .join(PurchaseItem.purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .where(*clauses)
        .cte("latest_item_description")
    )
    weighted = func.sum(PurchaseItem.quantity * PurchaseItem.unit_price)
    obs = (
        select(
            Store.cnpj.label("store_cnpj"),
            code.label("store_product_code"),
            identity_description,
            PurchaseItem.commercial_unit.label("commercial_unit"),
            Purchase.id.label("purchase_id"),
            FiscalDocument.issued_at.label("issued_at"),
            (weighted / func.nullif(func.sum(PurchaseItem.quantity), 0)).label("unit_price"),
        )
        .select_from(PurchaseItem)
        .join(PurchaseItem.purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .where(*clauses)
        .group_by(*dimensions, Purchase.id, FiscalDocument.issued_at)
        .cte("item_price_observations")
    )
    obs_ranked = (
        select(
            obs,
            func.row_number()
            .over(
                partition_by=(
                    obs.c.store_cnpj,
                    obs.c.store_product_code,
                    obs.c.identity_description,
                    obs.c.commercial_unit,
                ),
                order_by=(obs.c.issued_at.desc(), obs.c.purchase_id.desc()),
            )
            .label("rank"),
        ).cte("last_item_price")
    )
    latest = latest_ranked.alias("latest")
    last_price = obs_ranked.alias("last_price")
    filters = [latest.c.rank == 1, last_price.c.rank == 1]
    if q is not None:
        filters.append(latest.c.description.ilike(escaped_like(q), escape="\\"))
    joined = grouped.join(
        latest,
        and_(
            grouped.c.store_cnpj == latest.c.store_cnpj,
            grouped.c.store_product_code.is_not_distinct_from(latest.c.store_product_code),
            grouped.c.identity_description.is_not_distinct_from(latest.c.identity_description),
            grouped.c.commercial_unit == latest.c.commercial_unit,
        ),
    ).join(
        last_price,
        and_(
            grouped.c.store_cnpj == last_price.c.store_cnpj,
            grouped.c.store_product_code.is_not_distinct_from(last_price.c.store_product_code),
            grouped.c.identity_description.is_not_distinct_from(last_price.c.identity_description),
            grouped.c.commercial_unit == last_price.c.commercial_unit,
        ),
    )
    group_count, line_count, gross_amount = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(grouped.c.item_line_count), 0),
            func.coalesce(func.sum(grouped.c.gross_line_amount), 0),
        )
        .select_from(joined)
        .where(*filters)
    ).one()
    unit_rows = db.execute(
        select(grouped.c.commercial_unit, func.sum(grouped.c.quantity))
        .select_from(joined)
        .where(*filters)
        .group_by(grouped.c.commercial_unit)
        .order_by(grouped.c.commercial_unit.asc())
    ).all()
    if metric == "purchase_count":
        sort_col = grouped.c.purchase_count.desc()
    elif metric == "quantity":
        sort_col = grouped.c.quantity.desc()
    else:
        sort_col = grouped.c.gross_line_amount.desc()
    kind_sort = case((grouped.c.store_product_code.is_not(None), 0), else_=1)
    rows = db.execute(
        select(grouped, latest.c.description, last_price.c.unit_price)
        .select_from(joined)
        .where(*filters)
        .order_by(
            sort_col,
            grouped.c.store_cnpj.asc(),
            kind_sort.asc(),
            func.coalesce(grouped.c.store_product_code, "").asc(),
            func.coalesce(grouped.c.identity_description, "").asc(),
            grouped.c.commercial_unit.asc(),
        )
        .limit(limit)
    ).all()
    data = []
    for row in rows:
        data.append(
            {
                "identity": {
                    "kind": "store_code"
                    if row.store_product_code is not None
                    else "source_description",
                    "store_cnpj": row.store_cnpj,
                    "store_product_code": row.store_product_code,
                    "description": row.identity_description,
                    "commercial_unit": row.commercial_unit,
                },
                "description": row.description,
                "description_variants_count": int(row.description_variants_count),
                "item_line_count": int(row.item_line_count),
                "purchase_count": int(row.purchase_count),
                "quantity": precise(row.quantity),
                "commercial_unit": row.commercial_unit,
                "gross_line_amount": money(row.gross_line_amount),
                "first_issued_at": row.first_issued_at,
                "last_issued_at": row.last_issued_at,
                "last_unit_price": precise(row.unit_price) if row.unit_price is not None else None,
            }
        )
    return {
        "meta": {
            "date_from": period.date_from,
            "date_to": period.date_to,
            "timezone": TIMEZONE_NAME,
            "currency": "BRL",
            "store_cnpj": store_cnpj,
            "filters": {
                "store_cnpj": store_cnpj,
                "q": q.strip() if q is not None else None,
                "commercial_unit": commercial_unit,
                "metric": metric,
                "limit": str(limit),
            },
        },
        "metric": metric,
        "summary": {
            "group_count": int(group_count or 0),
            "item_line_count": int(line_count or 0),
            "gross_line_amount": money(gross_amount),
            "quantity_by_unit": [
                {"commercial_unit": unit, "quantity": precise(quantity)}
                for unit, quantity in unit_rows
            ],
        },
        "data": data,
    }


def processing_dashboard(
    db: Session, *, period: Period, capture_method: str | None, bucket: str
) -> dict:
    clauses = [Processing.created_at >= period.start, Processing.created_at < period.end_exclusive]
    if capture_method:
        clauses.append(Processing.capture_method == capture_method)
    is_success = case(
        (
            (Processing.state == ProcessingState.COMPLETED)
            & Processing.outcome.in_(SUCCESS_OUTCOMES),
            1,
        ),
        else_=0,
    )
    is_failed = case((Processing.state == ProcessingState.FAILED, 1), else_=0)
    is_rejected = case((Processing.state == ProcessingState.REJECTED, 1), else_=0)
    is_in_progress = case(
        (
            Processing.state.not_in(
                (ProcessingState.COMPLETED, ProcessingState.FAILED, ProcessingState.REJECTED)
            ),
            1,
        ),
        else_=0,
    )
    existing_return = case(
        (Processing.outcome.in_((ProcessingOutcome.EXISTING, ProcessingOutcome.CANCELLED_EXISTING)), 1),
        else_=0,
    )
    terminal = (is_success == 1) | (is_failed == 1) | (is_rejected == 1)
    summary_row = db.execute(
        select(
            func.count(Processing.id),
            func.sum(is_success),
            func.sum(is_failed),
            func.sum(is_rejected),
            func.sum(is_in_progress),
            func.sum(existing_return),
            func.sum(case((terminal, 1), else_=0)),
        )
        .select_from(Processing)
        .where(*clauses)
    ).one()
    # Percentiles are computed on the terminal subset with non-null duration only.
    percentile_row = db.execute(
        select(
            func.cast(
                func.percentile_cont(0.50).within_group(Processing.duration_ms), Numeric(16, 2)
            ),
            func.cast(
                func.percentile_cont(0.95).within_group(Processing.duration_ms), Numeric(16, 2)
            ),
        )
        .select_from(Processing)
        .where(*clauses, terminal, Processing.duration_ms.is_not(None))
    ).one()
    total_count = int(summary_row[0] or 0)
    success = int(summary_row[1] or 0)
    failed = int(summary_row[2] or 0)
    rejected = int(summary_row[3] or 0)
    in_progress = int(summary_row[4] or 0)
    returns = int(summary_row[5] or 0)
    terminal_count = int(summary_row[6] or 0)
    success_rate = percent(Decimal(success) / terminal_count * 100) if terminal_count else None
    summary = {
        "processing_count": total_count,
        "successful_count": success,
        "failed_count": failed,
        "rejected_count": rejected,
        "in_progress_count": in_progress,
        "existing_return_count": returns,
        "success_rate_percent": success_rate,
        "duration_p50_ms": percent(percentile_row[0]) if percentile_row[0] is not None else None,
        "duration_p95_ms": percent(percentile_row[1]) if percentile_row[1] is not None else None,
    }
    outcomes = db.execute(
        select(Processing.outcome, func.count(Processing.id))
        .where(*clauses)
        .group_by(Processing.outcome)
        .order_by(Processing.outcome.asc().nulls_last())
    ).all()
    errors = db.execute(
        select(Processing.error_code, func.count(Processing.id))
        .where(
            *clauses,
            Processing.state.in_((ProcessingState.FAILED, ProcessingState.REJECTED)),
            Processing.error_code.is_not(None),
        )
        .group_by(Processing.error_code)
        .order_by(func.count(Processing.id).desc(), Processing.error_code.asc())
    ).all()
    local_created = func.timezone(TIMEZONE_NAME, Processing.created_at)
    bucket_expr = func.date_trunc(bucket, local_created).label("bucket")
    bucket_rows = db.execute(
        select(
            bucket_expr,
            func.count(Processing.id),
            func.sum(is_success),
            func.sum(is_failed),
            func.sum(is_rejected),
            func.sum(is_in_progress),
        )
        .where(*clauses)
        .group_by(bucket_expr)
    ).all()
    grouped = {
        clipped_bucket_start(row[0].date(), period, bucket): row[1:] for row in bucket_rows
    }
    data = []
    for start, end in make_buckets(period, bucket):
        values = grouped.get(start, (0, 0, 0, 0, 0))
        bucket_count, bucket_success, bucket_failed, bucket_rejected, bucket_progress = map(
            lambda value: int(value or 0), values
        )
        bucket_terminal = bucket_success + bucket_failed + bucket_rejected
        data.append(
            {
                "bucket_start": start,
                "bucket_end": end,
                "processing_count": bucket_count,
                "successful_count": bucket_success,
                "failed_count": bucket_failed,
                "rejected_count": bucket_rejected,
                "in_progress_count": bucket_progress,
                "success_rate_percent": percent(Decimal(bucket_success) / bucket_terminal * 100)
                if bucket_terminal
                else None,
            }
        )
    return {
        "meta": {
            "date_from": period.date_from,
            "date_to": period.date_to,
            "timezone": TIMEZONE_NAME,
            "currency": "BRL",
            "filters": {"capture_method": capture_method, "bucket": bucket},
        },
        "bucket": bucket,
        "summary": summary,
        "outcomes": [{"result": outcome, "count": int(count)} for outcome, count in outcomes],
        "error_distribution": [
            {"error_code": code, "count": int(count)} for code, count in errors
        ],
        "data": data,
    }
