from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from marketracking.db.models import FiscalDocument, Purchase, PurchaseItem, PurchaseTotal, Store
from marketracking.schemas.nfce import FiscalDocumentStatus
from marketracking.services.query_common import (
    Period,
    TIMEZONE_NAME,
    clipped_bucket_start,
    escaped_like,
    make_buckets,
    money,
    percent,
    precise,
    validate_cnpj,
)


def _authorized_clauses(period: Period, store_cnpj: str | None = None) -> list:
    clauses = [
        FiscalDocument.issued_at >= period.start,
        FiscalDocument.issued_at < period.end_exclusive,
        FiscalDocument.status == FiscalDocumentStatus.AUTHORIZED,
        Purchase.status == FiscalDocumentStatus.AUTHORIZED,
    ]
    if store_cnpj is not None:
        clauses.append(Store.cnpj == validate_cnpj(store_cnpj))
    return clauses


def _authorized_history_clauses(store_cnpj: str, commercial_unit: str) -> list:
    return [
        FiscalDocument.status == FiscalDocumentStatus.AUTHORIZED,
        Purchase.status == FiscalDocumentStatus.AUTHORIZED,
        Store.cnpj == validate_cnpj(store_cnpj),
        PurchaseItem.commercial_unit == commercial_unit,
    ]


def _meta(
    period: Period,
    store_cnpj: str | None = None,
    filters: dict[str, str | None] | None = None,
) -> dict:
    return {
        "date_from": period.date_from,
        "date_to": period.date_to,
        "timezone": TIMEZONE_NAME,
        "currency": "BRL",
        "store_cnpj": store_cnpj,
        "filters": filters or {},
    }


def spending_report(
    db: Session,
    *,
    period: Period,
    store_cnpj: str | None,
    group_by: str,
    limit: int,
) -> dict:
    clauses = _authorized_clauses(period, store_cnpj)
    summary_row = db.execute(
        select(
            func.count(Purchase.id),
            func.coalesce(func.sum(PurchaseTotal.gross_items_amount), 0),
            func.coalesce(func.sum(PurchaseTotal.total_discount), 0),
            func.coalesce(func.sum(PurchaseTotal.payable_amount), 0),
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
    (
        purchase_count,
        gross,
        discount,
        payable,
        line_count,
        store_count,
        known_taxes,
        purchases_with_tax,
    ) = summary_row
    purchase_count = int(purchase_count or 0)
    total_payable = money(payable)
    summary = {
        "purchase_count": purchase_count,
        "gross_items_amount": money(gross),
        "total_discount": money(discount),
        "payable_amount": total_payable,
        "average_ticket": money(total_payable / purchase_count) if purchase_count else money(0),
        "item_line_count": int(line_count or 0),
        "store_count": int(store_count or 0),
        "known_taxes_amount": money(known_taxes) if purchases_with_tax else None,
        "purchases_with_tax_info": int(purchases_with_tax or 0),
    }

    if group_by == "store":
        rows = db.execute(
            select(
                Store.cnpj,
                Store.name,
                func.max(FiscalDocument.issued_at),
                func.count(Purchase.id),
                func.sum(PurchaseTotal.payable_amount),
            )
            .select_from(Purchase)
            .join(Purchase.fiscal_document)
            .join(Purchase.store)
            .join(Purchase.total)
            .where(*clauses)
            .group_by(Store.cnpj, Store.name)
            .order_by(func.sum(PurchaseTotal.payable_amount).desc(), Store.cnpj.asc())
        ).all()
        data = []
        for cnpj, name, last_at, count, amount in rows[:limit]:
            amount = money(amount)
            data.append(
                {
                    "store_cnpj": cnpj,
                    "store_name": name,
                    "last_purchase_at": last_at,
                    "purchase_count": int(count),
                    "payable_amount": amount,
                    "share_percent": percent(amount / total_payable * 100)
                    if total_payable
                    else None,
                }
            )
        other_rows = rows[limit:]
        other_count = len(other_rows)
        other_purchases = sum(int(row[3]) for row in other_rows)
        other_payable = money(sum((Decimal(row[4] or 0) for row in other_rows), Decimal(0)))
        others = {
            "store_count": other_count,
            "purchase_count": other_purchases,
            "payable_amount": other_payable,
            "share_percent": percent(other_payable / total_payable * 100)
            if total_payable
            else None,
        }
        return {
            "meta": _meta(
                period,
                store_cnpj,
                {"group_by": group_by, "limit": str(limit), "store_cnpj": store_cnpj},
            ),
            "group_by": group_by,
            "summary": summary,
            "data": data,
            "others": others,
        }

    local_issued = func.timezone(TIMEZONE_NAME, FiscalDocument.issued_at)
    bucket_expr = func.date_trunc(group_by, local_issued).label("bucket")
    rows = db.execute(
        select(
            bucket_expr,
            func.count(Purchase.id),
            func.sum(PurchaseTotal.gross_items_amount),
            func.sum(PurchaseTotal.total_discount),
            func.sum(PurchaseTotal.payable_amount),
        )
        .select_from(Purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .join(Purchase.total)
        .where(*clauses)
        .group_by(bucket_expr)
    ).all()
    values = {
        clipped_bucket_start(row[0].date(), period, group_by): row[1:] for row in rows
    }
    data = []
    for bucket_start, bucket_end in make_buckets(period, group_by):
        values_for_bucket = values.get(bucket_start)
        if values_for_bucket is None:
            count, bucket_gross, bucket_discount, bucket_payable = 0, 0, 0, 0
        else:
            count, bucket_gross, bucket_discount, bucket_payable = values_for_bucket
        count = int(count or 0)
        bucket_payable = money(bucket_payable)
        data.append(
            {
                "bucket_start": bucket_start,
                "bucket_end": bucket_end,
                "purchase_count": count,
                "gross_items_amount": money(bucket_gross),
                "total_discount": money(bucket_discount),
                "payable_amount": bucket_payable,
                "average_ticket": money(bucket_payable / count) if count else money(0),
            }
        )
    return {
        "meta": _meta(
            period,
            store_cnpj,
            {"group_by": group_by, "store_cnpj": store_cnpj},
        ),
        "group_by": group_by,
        "summary": summary,
        "data": data,
    }


def _price_observations(
    store_cnpj: str,
    commercial_unit: str,
    *,
    store_product_code: str | None,
    description: str | None,
    period: Period,
):
    clauses = _authorized_clauses(period, store_cnpj)
    clauses.append(PurchaseItem.commercial_unit == commercial_unit)
    if store_product_code is not None:
        clauses.append(PurchaseItem.store_product_code == store_product_code)
    else:
        clauses.extend(
            [PurchaseItem.store_product_code.is_(None), PurchaseItem.description == description]
        )
    weighted_price = func.sum(PurchaseItem.quantity * PurchaseItem.unit_price)
    quantity = func.sum(PurchaseItem.quantity)
    price = (weighted_price / func.nullif(quantity, 0)).label("observed_unit_price")
    description_line = select(PurchaseItem.description).where(
        PurchaseItem.purchase_id == Purchase.id,
        PurchaseItem.commercial_unit == commercial_unit,
    )
    if store_product_code is not None:
        description_line = description_line.where(
            PurchaseItem.store_product_code == store_product_code
        )
    else:
        description_line = description_line.where(
            PurchaseItem.store_product_code.is_(None), PurchaseItem.description == description
        )
    representative_description = (
        description_line.order_by(PurchaseItem.line_number.desc(), PurchaseItem.id.desc())
        .limit(1)
        .correlate(Purchase)
        .scalar_subquery()
    )
    rows = (
        select(
            Purchase.id.label("purchase_id"),
            FiscalDocument.issued_at.label("issued_at"),
            representative_description.label("description"),
            price,
            quantity.label("quantity"),
            func.count(PurchaseItem.id).label("item_line_count"),
            weighted_price.label("weighted_price"),
        )
        .select_from(PurchaseItem)
        .join(PurchaseItem.purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .where(*clauses)
        .group_by(Purchase.id, FiscalDocument.issued_at)
        .subquery("price_observations")
    )
    return rows


def price_report(
    db: Session,
    *,
    period: Period,
    store_cnpj: str,
    commercial_unit: str,
    store_product_code: str | None,
    description: str | None,
    bucket: str,
    page: int,
    page_size: int,
) -> dict:
    store_cnpj = validate_cnpj(store_cnpj)
    observations = _price_observations(
        store_cnpj,
        commercial_unit,
        store_product_code=store_product_code,
        description=description,
        period=period,
    )
    summary_values = db.execute(
        select(
            func.count(observations.c.purchase_id),
            func.coalesce(func.sum(observations.c.quantity), 0),
            func.min(observations.c.observed_unit_price),
            func.max(observations.c.observed_unit_price),
            func.sum(observations.c.weighted_price),
        )
    ).one()
    observation_count = int(summary_values[0] or 0)
    quantity_total = precise(summary_values[1])
    weighted_average = (
        precise(Decimal(summary_values[4]) / Decimal(summary_values[1]))
        if summary_values[1]
        else None
    )
    last_two = db.execute(
        select(
            observations.c.purchase_id,
            observations.c.issued_at,
            observations.c.observed_unit_price,
        )
        .order_by(observations.c.issued_at.desc(), observations.c.purchase_id.desc())
        .limit(2)
    ).all()
    last_price = precise(last_two[0][2]) if last_two else None
    last_issued_at = last_two[0][1] if last_two else None
    delta = precise(last_two[0][2] - last_two[1][2]) if len(last_two) == 2 else None
    delta_percent = (
        percent((last_two[0][2] - last_two[1][2]) / last_two[1][2] * 100)
        if len(last_two) == 2 and last_two[1][2] != 0
        else None
    )
    summary = {
        "observation_count": observation_count,
        "quantity": quantity_total,
        "minimum_unit_price": precise(summary_values[2]) if summary_values[2] is not None else None,
        "maximum_unit_price": precise(summary_values[3]) if summary_values[3] is not None else None,
        "weighted_average_unit_price": weighted_average,
        "last_unit_price": last_price,
        "last_issued_at": last_issued_at,
        "delta": delta,
        "delta_percent": delta_percent,
    }
    identity = {
        "kind": "store_code" if store_product_code is not None else "source_description",
        "store_cnpj": store_cnpj,
        "store_product_code": store_product_code,
        "description": description if store_product_code is None else None,
        "commercial_unit": commercial_unit,
    }
    if bucket == "purchase":
        total_pages = (observation_count + page_size - 1) // page_size
        rows = db.execute(
            select(observations)
            .order_by(observations.c.issued_at.asc(), observations.c.purchase_id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        data = [
            {
                "purchase_id": row.purchase_id,
                "issued_at": row.issued_at,
                "description": row.description,
                "observed_unit_price": precise(row.observed_unit_price),
                "quantity": precise(row.quantity),
                "item_line_count": int(row.item_line_count),
            }
            for row in rows
        ]
        page_data = {
            "page": page,
            "page_size": page_size,
            "total": observation_count,
            "total_pages": total_pages,
        }
    else:
        local_issued = func.timezone(TIMEZONE_NAME, observations.c.issued_at)
        bucket_expr = func.date_trunc(bucket, local_issued).label("bucket")
        rows = db.execute(
            select(
                bucket_expr,
                func.sum(observations.c.weighted_price),
                func.sum(observations.c.quantity),
                func.count(observations.c.purchase_id),
            ).group_by(bucket_expr)
        ).all()
        by_bucket = {
            clipped_bucket_start(row[0].date(), period, bucket): row[1:] for row in rows
        }
        data = []
        for bucket_start, bucket_end in make_buckets(period, bucket):
            values = by_bucket.get(bucket_start)
            if values is None:
                bucket_price = None
                bucket_quantity = Decimal(0)
                bucket_count = 0
            else:
                weighted, bucket_quantity, bucket_count = values
                bucket_price = precise(Decimal(weighted) / Decimal(bucket_quantity))
            data.append(
                {
                    "bucket_start": bucket_start,
                    "bucket_end": bucket_end,
                    "observed_unit_price": bucket_price,
                    "quantity": precise(bucket_quantity),
                    "observation_count": int(bucket_count),
                }
            )
        page_data = None
    result = {"meta": {"date_from": period.date_from, "date_to": period.date_to,
                       "timezone": TIMEZONE_NAME, "currency": "BRL", "store_cnpj": store_cnpj,
                       "filters": {"store_cnpj": store_cnpj, "commercial_unit": commercial_unit,
                                   "store_product_code": store_product_code,
                                   "description": description, "bucket": bucket}},
              "identity": identity, "summary": summary, "data": data}
    if page_data is not None:
        result["pagination"] = page_data
    return result


def price_changes(
    db: Session,
    *,
    period: Period,
    store_cnpj: str | None,
    commercial_unit: str | None,
    q: str | None,
    direction: str,
    limit: int,
) -> dict:
    store_clause = validate_cnpj(store_cnpj) if store_cnpj is not None else None
    identity_description = case(
        (PurchaseItem.store_product_code.is_not(None), None), else_=PurchaseItem.description
    ).label("identity_description")
    where = [
        FiscalDocument.status == FiscalDocumentStatus.AUTHORIZED,
        Purchase.status == FiscalDocumentStatus.AUTHORIZED,
    ]
    if store_clause is not None:
        where.append(Store.cnpj == store_clause)
    if commercial_unit is not None:
        where.append(PurchaseItem.commercial_unit == commercial_unit)
    dimensions = (
        Store.cnpj,
        PurchaseItem.store_product_code,
        identity_description,
        PurchaseItem.commercial_unit,
    )
    representative_ranked = (
        select(
            Store.cnpj.label("store_cnpj"),
            PurchaseItem.store_product_code.label("store_product_code"),
            identity_description,
            PurchaseItem.commercial_unit.label("commercial_unit"),
            Purchase.id.label("purchase_id"),
            PurchaseItem.description.label("description"),
            func.row_number()
            .over(
                partition_by=(*dimensions, Purchase.id),
                order_by=(PurchaseItem.line_number.desc(), PurchaseItem.id.desc()),
            )
            .label("description_rank"),
        )
        .select_from(PurchaseItem)
        .join(PurchaseItem.purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .where(*where)
        .cte("representative_description_ranked")
    )
    representative_descriptions = (
        select(representative_ranked)
        .where(representative_ranked.c.description_rank == 1)
        .cte("representative_descriptions")
    )
    weighted = func.sum(PurchaseItem.quantity * PurchaseItem.unit_price)
    quantity = func.sum(PurchaseItem.quantity)
    grouped_observations = (
        select(
            Store.cnpj.label("store_cnpj"),
            PurchaseItem.store_product_code.label("store_product_code"),
            identity_description,
            PurchaseItem.commercial_unit.label("commercial_unit"),
            Purchase.id.label("purchase_id"),
            FiscalDocument.issued_at.label("issued_at"),
            (weighted / func.nullif(quantity, 0)).label("observed_unit_price"),
        )
        .select_from(PurchaseItem)
        .join(PurchaseItem.purchase)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .where(*where)
        .group_by(*dimensions, Purchase.id, FiscalDocument.issued_at)
        .cte("grouped_observations")
    )
    observations = (
        select(
            grouped_observations.c.store_cnpj,
            grouped_observations.c.store_product_code,
            grouped_observations.c.identity_description,
            grouped_observations.c.commercial_unit,
            grouped_observations.c.purchase_id,
            grouped_observations.c.issued_at,
            representative_descriptions.c.description,
            grouped_observations.c.observed_unit_price,
        )
        .join(
            representative_descriptions,
            and_(
                grouped_observations.c.store_cnpj
                == representative_descriptions.c.store_cnpj,
                grouped_observations.c.store_product_code.is_not_distinct_from(
                    representative_descriptions.c.store_product_code
                ),
                grouped_observations.c.identity_description.is_not_distinct_from(
                    representative_descriptions.c.identity_description
                ),
                grouped_observations.c.commercial_unit
                == representative_descriptions.c.commercial_unit,
                grouped_observations.c.purchase_id == representative_descriptions.c.purchase_id,
            ),
        )
        .cte("observations")
    )
    in_period = and_(
        observations.c.issued_at >= period.start,
        observations.c.issued_at < period.end_exclusive,
    )
    current_ranked = (
        select(
            observations,
            func.row_number()
            .over(
                partition_by=(
                    observations.c.store_cnpj,
                    observations.c.store_product_code,
                    observations.c.identity_description,
                    observations.c.commercial_unit,
                ),
                order_by=(observations.c.issued_at.desc(), observations.c.purchase_id.desc()),
            )
            .label("current_rank"),
        )
        .where(in_period)
        .cte("current_ranked")
    )
    current = current_ranked.alias("current")
    previous_match = and_(
        observations.c.store_cnpj == current.c.store_cnpj,
        observations.c.store_product_code.is_not_distinct_from(current.c.store_product_code),
        observations.c.identity_description.is_not_distinct_from(current.c.identity_description),
        observations.c.commercial_unit == current.c.commercial_unit,
        or_(
            observations.c.issued_at < current.c.issued_at,
            and_(
                observations.c.issued_at == current.c.issued_at,
                observations.c.purchase_id < current.c.purchase_id,
            ),
        ),
    )
    previous = (
        select(observations)
        .where(previous_match)
        .order_by(observations.c.issued_at.desc(), observations.c.purchase_id.desc())
        .limit(1)
        .correlate(current)
    )
    previous_id = previous.with_only_columns(observations.c.purchase_id).scalar_subquery()
    previous_date = previous.with_only_columns(observations.c.issued_at).scalar_subquery()
    previous_price = previous.with_only_columns(observations.c.observed_unit_price).scalar_subquery()
    delta_expr = (current.c.observed_unit_price - previous_price).label("delta")
    delta_percent_expr = (
        (current.c.observed_unit_price - previous_price) / func.nullif(previous_price, 0) * 100
    ).label("delta_percent")
    desc_filter = escaped_like(q) if q is not None else None
    condition = [current.c.current_rank == 1]
    if desc_filter:
        condition.append(current.c.description.ilike(desc_filter, escape="\\"))
    priced = (
        select(
            current.c.store_cnpj,
            current.c.store_product_code,
            current.c.identity_description,
            current.c.commercial_unit,
            current.c.description,
            current.c.purchase_id.label("current_purchase_id"),
            current.c.issued_at.label("current_issued_at"),
            current.c.observed_unit_price.label("current_price"),
            previous_id.label("previous_purchase_id"),
            previous_date.label("previous_issued_at"),
            previous_price.label("previous_price"),
            delta_expr,
            delta_percent_expr,
        )
        .where(*condition)
        .cte("priced_changes")
    )
    has_previous = priced.c.previous_purchase_id.is_not(None)
    unchanged_condition = has_previous & (priced.c.current_price == priced.c.previous_price)
    zero_baseline_condition = has_previous & (priced.c.previous_price == 0)
    changed_condition = (
        has_previous
        & (priced.c.previous_price != 0)
        & (priced.c.current_price != priced.c.previous_price)
    )
    direction_condition = None
    if direction == "increase":
        direction_condition = priced.c.delta > 0
        order_by = (priced.c.delta_percent.desc(),)
    elif direction == "decrease":
        direction_condition = priced.c.delta < 0
        order_by = (priced.c.delta_percent.asc(),)
    else:
        order_by = (func.abs(priced.c.delta_percent).desc(),)
    ranked_change = changed_condition
    if direction_condition is not None:
        ranked_change = ranked_change & direction_condition
    no_previous, unchanged, zero_baseline, changed_count = db.execute(
        select(
            func.coalesce(func.sum(case((priced.c.previous_purchase_id.is_(None), 1), else_=0)), 0),
            func.coalesce(func.sum(case((unchanged_condition, 1), else_=0)), 0),
            func.coalesce(func.sum(case((zero_baseline_condition, 1), else_=0)), 0),
            func.coalesce(func.sum(case((ranked_change, 1), else_=0)), 0),
        ).select_from(priced)
    ).one()
    kind_order = case((priced.c.store_product_code.is_not(None), 0), else_=1)
    rows = db.execute(
        select(priced)
        .where(ranked_change)
        .order_by(
            *order_by,
            priced.c.store_cnpj.asc(),
            kind_order.asc(),
            func.coalesce(priced.c.store_product_code, "").asc(),
            func.coalesce(priced.c.identity_description, "").asc(),
            priced.c.commercial_unit.asc(),
        )
        .limit(limit)
    ).all()
    data = []
    for row in rows:
        identity = {
            "kind": "store_code" if row.store_product_code is not None else "source_description",
            "store_cnpj": row.store_cnpj,
            "store_product_code": row.store_product_code,
            "description": row.identity_description,
            "commercial_unit": row.commercial_unit,
        }
        data.append(
            {
                "identity": identity,
                "description": row.description,
                "current": {
                    "purchase_id": row.current_purchase_id,
                    "issued_at": row.current_issued_at,
                    "observed_unit_price": precise(row.current_price),
                },
                "previous": {
                    "purchase_id": row.previous_purchase_id,
                    "issued_at": row.previous_issued_at,
                    "observed_unit_price": precise(row.previous_price),
                },
                "delta": precise(row.delta),
                "delta_percent": percent(row.delta_percent),
            }
        )
    return {
        "meta": _meta(
            period,
            store_clause,
            {
                "store_cnpj": store_clause,
                "commercial_unit": commercial_unit,
                "q": q.strip() if q is not None else None,
                "direction": direction,
                "limit": str(limit),
            },
        ),
        "summary": {
            "no_previous_count": int(no_previous),
            "unchanged_count": int(unchanged),
            "zero_baseline_count": int(zero_baseline),
            "changed_group_count": int(changed_count),
        },
        "data": data,
    }
