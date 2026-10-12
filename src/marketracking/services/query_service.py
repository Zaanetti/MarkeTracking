from __future__ import annotations

import math
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from marketracking.db.models import (
    FiscalDocument,
    Processing,
    Purchase,
    PurchaseItem,
    PurchaseTotal,
    Store,
)
from marketracking.schemas.nfce import FiscalDocumentStatus
from marketracking.schemas.queries import Pagination
from marketracking.services.query_common import (
    DateBounds,
    escaped_like,
    money,
    validate_access_key,
    validate_cnpj,
)


def pagination(page: int, page_size: int, total: int) -> Pagination:
    return Pagination(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


def _period_clauses(column, period: DateBounds | None) -> list:
    if period is None:
        return []
    clauses = []
    if period.start is not None:
        clauses.append(column >= period.start)
    if period.end_exclusive is not None:
        clauses.append(column < period.end_exclusive)
    return clauses


def _purchase_status_clauses(status: str) -> list:
    if status == "authorized":
        return [
            Purchase.status == FiscalDocumentStatus.AUTHORIZED,
            FiscalDocument.status == FiscalDocumentStatus.AUTHORIZED,
        ]
    if status == "cancelled":
        return [
            or_(
                Purchase.status == FiscalDocumentStatus.CANCELLED,
                FiscalDocument.status == FiscalDocumentStatus.CANCELLED,
            )
        ]
    return []


def _purchase_filters(
    *,
    period: DateBounds | None,
    store_cnpj: str | None,
    status: str,
    access_key: str | None = None,
) -> list:
    clauses = _period_clauses(FiscalDocument.issued_at, period)
    clauses.extend(_purchase_status_clauses(status))
    if store_cnpj:
        clauses.append(Store.cnpj == validate_cnpj(store_cnpj))
    if access_key:
        clauses.append(FiscalDocument.access_key == validate_access_key(access_key))
    return clauses


def list_purchases(
    db: Session,
    *,
    period: DateBounds | None,
    store_cnpj: str | None,
    access_key: str | None,
    status: str,
    min_total: Decimal | None,
    max_total: Decimal | None,
    sort: str,
    page: int,
    page_size: int,
) -> dict:
    if min_total is not None and min_total < 0 or max_total is not None and max_total < 0:
        raise ValueError("Os valores mínimo e máximo não podem ser negativos.")
    if min_total is not None and max_total is not None and min_total > max_total:
        raise ValueError("min_total deve ser menor ou igual a max_total.")
    clauses = _purchase_filters(
        period=period,
        store_cnpj=store_cnpj,
        status=status,
        access_key=access_key,
    )
    if min_total is not None:
        clauses.append(PurchaseTotal.payable_amount >= min_total)
    if max_total is not None:
        clauses.append(PurchaseTotal.payable_amount <= max_total)
    base = (
        select(Purchase, FiscalDocument, Store, PurchaseTotal)
        .join(Purchase.fiscal_document)
        .join(Purchase.store)
        .join(Purchase.total)
        .where(*clauses)
    )
    total = db.scalar(
        select(func.count()).select_from(
            select(Purchase.id)
            .join(Purchase.fiscal_document)
            .join(Purchase.store)
            .join(Purchase.total)
            .where(*clauses)
            .subquery()
        )
    ) or 0
    if sort == "issued_at_asc":
        ordering = (FiscalDocument.issued_at.asc(), Purchase.id.asc())
    elif sort == "payable_amount_desc":
        ordering = (PurchaseTotal.payable_amount.desc(), Purchase.id.asc())
    elif sort == "payable_amount_asc":
        ordering = (PurchaseTotal.payable_amount.asc(), Purchase.id.asc())
    else:
        ordering = (FiscalDocument.issued_at.desc(), Purchase.id.asc())
    rows = db.execute(
        base.order_by(*ordering).offset((page - 1) * page_size).limit(page_size)
    ).all()
    data = [
        {
            "purchase_id": purchase.id,
            "fiscal_document_id": document.id,
            "issued_at": document.issued_at,
            "status": (
                FiscalDocumentStatus.CANCELLED
                if purchase.status == FiscalDocumentStatus.CANCELLED
                or document.status == FiscalDocumentStatus.CANCELLED
                else FiscalDocumentStatus.AUTHORIZED
            ),
            "store_cnpj": store.cnpj,
            "issued_store_name": purchase.issued_store_name,
            "item_line_count": total_row.declared_line_count,
            "gross_items_amount": money(total_row.gross_items_amount),
            "total_discount": money(total_row.total_discount),
            "payable_amount": money(total_row.payable_amount),
            "total_taxes": money(total_row.total_taxes) if total_row.total_taxes is not None else None,
        }
        for purchase, document, store, total_row in rows
    ]
    return {"data": data, "pagination": pagination(page, page_size, total)}


def get_purchase(db: Session, purchase_id: UUID) -> dict | None:
    purchase = db.scalar(
        select(Purchase)
        .where(Purchase.id == purchase_id)
        .options(
            selectinload(Purchase.fiscal_document),
            selectinload(Purchase.store),
            selectinload(Purchase.items),
            selectinload(Purchase.total),
        )
    )
    if purchase is None:
        return None
    document = purchase.fiscal_document
    store = purchase.store
    total = purchase.total
    if document is None or store is None or total is None:
        return None
    return {
        "purchase_id": purchase.id,
        "fiscal_document": {
            "fiscal_document_id": document.id,
            "access_key": document.access_key,
            "number": document.number,
            "series": document.series,
            "issued_at": document.issued_at,
            "authorization_protocol": document.authorization_protocol,
            "authorized_at": document.authorized_at,
            "status": document.status,
            "environment": document.environment,
            "xml_version": document.xml_version,
            "xslt_version": document.xslt_version,
        },
        "store": {
            "id": store.id,
            "cnpj": store.cnpj,
            "name": store.name,
            "address": store.address,
            "details_as_of": store.details_as_of,
        },
        "issued_store_details": {
            "name": purchase.issued_store_name,
            "address": purchase.issued_store_address,
        },
        "items": [
            {
                "item_id": item.id,
                "line_number": item.line_number,
                "store_product_code": item.store_product_code,
                "description": item.description,
                "quantity": item.quantity,
                "commercial_unit": item.commercial_unit,
                "unit_price": item.unit_price,
                "line_total": money(item.line_total),
            }
            for item in sorted(purchase.items, key=lambda item: (item.line_number, item.id))
        ],
        "total": {
            "declared_line_count": total.declared_line_count,
            "gross_items_amount": money(total.gross_items_amount),
            "total_discount": money(total.total_discount),
            "payable_amount": money(total.payable_amount),
            "total_taxes": money(total.total_taxes) if total.total_taxes is not None else None,
        },
    }


def list_purchase_items(
    db: Session,
    *,
    period: DateBounds | None,
    purchase_id: UUID | None,
    store_cnpj: str | None,
    store_product_code: str | None,
    q: str | None,
    commercial_unit: str | None,
    status: str,
    sort: str,
    page: int,
    page_size: int,
) -> dict:
    clauses = _purchase_filters(
        period=period,
        store_cnpj=store_cnpj,
        status=status,
    )
    if purchase_id is not None:
        clauses.append(Purchase.id == purchase_id)
    if store_product_code is not None:
        clauses.append(PurchaseItem.store_product_code == store_product_code)
    if q is not None:
        clauses.append(PurchaseItem.description.ilike(escaped_like(q), escape="\\"))
    if commercial_unit is not None:
        clauses.append(PurchaseItem.commercial_unit == commercial_unit)
    from_clause = (
        PurchaseItem.__table__
        .join(Purchase.__table__, PurchaseItem.purchase_id == Purchase.id)
        .join(FiscalDocument.__table__, Purchase.fiscal_document_id == FiscalDocument.id)
        .join(Store.__table__, Purchase.store_id == Store.id)
    )
    count = db.scalar(select(func.count()).select_from(from_clause).where(*clauses)) or 0
    if sort == "issued_at_asc":
        ordering = (FiscalDocument.issued_at.asc(), Purchase.id.asc(), PurchaseItem.line_number.asc())
    elif sort == "unit_price_asc":
        ordering = (PurchaseItem.unit_price.asc(), Purchase.id.asc(), PurchaseItem.line_number.asc())
    elif sort == "unit_price_desc":
        ordering = (PurchaseItem.unit_price.desc(), Purchase.id.asc(), PurchaseItem.line_number.asc())
    else:
        ordering = (
            FiscalDocument.issued_at.desc(),
            Purchase.id.asc(),
            PurchaseItem.line_number.asc(),
        )
    rows = db.execute(
        select(PurchaseItem, Purchase, FiscalDocument, Store)
        .select_from(from_clause)
        .where(*clauses)
        .order_by(*ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    data = [
        {
            "item_id": item.id,
            "purchase_id": purchase.id,
            "issued_at": document.issued_at,
            "status": (
                FiscalDocumentStatus.CANCELLED
                if purchase.status == FiscalDocumentStatus.CANCELLED
                or document.status == FiscalDocumentStatus.CANCELLED
                else FiscalDocumentStatus.AUTHORIZED
            ),
            "store_cnpj": store.cnpj,
            "issued_store_name": purchase.issued_store_name,
            "line_number": item.line_number,
            "store_product_code": item.store_product_code,
            "description": item.description,
            "quantity": item.quantity,
            "commercial_unit": item.commercial_unit,
            "unit_price": item.unit_price,
            "line_total": money(item.line_total),
        }
        for item, purchase, document, store in rows
    ]
    return {"data": data, "pagination": pagination(page, page_size, count)}


def _store_summary_subquery(period: DateBounds | None):
    clauses = [
        Purchase.status == FiscalDocumentStatus.AUTHORIZED,
        FiscalDocument.status == FiscalDocumentStatus.AUTHORIZED,
        *_period_clauses(FiscalDocument.issued_at, period),
    ]
    return (
        select(
            Purchase.store_id.label("store_id"),
            func.count(Purchase.id).label("purchase_count"),
            func.sum(PurchaseTotal.payable_amount).label("payable_amount"),
            func.max(FiscalDocument.issued_at).label("last_purchase_at"),
        )
        .join(Purchase.fiscal_document)
        .join(Purchase.total)
        .where(*clauses)
        .group_by(Purchase.store_id)
        .subquery()
    )


def list_stores(
    db: Session,
    *,
    period: DateBounds | None,
    q: str | None,
    cnpj: str | None,
    sort: str,
    page: int,
    page_size: int,
) -> dict:
    summary = _store_summary_subquery(period)
    clauses = []
    if q is not None:
        like = escaped_like(q)
        clauses.append(Store.name.ilike(like, escape="\\"))
    if cnpj is not None:
        clauses.append(Store.cnpj == validate_cnpj(cnpj))
    total = db.scalar(select(func.count()).select_from(Store).where(*clauses)) or 0
    if sort == "payable_amount_desc":
        ordering = (func.coalesce(summary.c.payable_amount, 0).desc(), Store.cnpj.asc())
    elif sort == "purchase_count_desc":
        ordering = (func.coalesce(summary.c.purchase_count, 0).desc(), Store.cnpj.asc())
    else:
        ordering = (Store.name.asc(), Store.cnpj.asc())
    rows = db.execute(
        select(Store, summary.c.purchase_count, summary.c.payable_amount, summary.c.last_purchase_at)
        .outerjoin(summary, summary.c.store_id == Store.id)
        .where(*clauses)
        .order_by(*ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    data = [
        {
            "id": store.id,
            "cnpj": store.cnpj,
            "name": store.name,
            "address": store.address,
            "details_as_of": store.details_as_of,
            "summary": {
                "purchase_count": int(count or 0),
                "payable_amount": money(amount),
                "last_purchase_at": last_at,
            },
        }
        for store, count, amount, last_at in rows
    ]
    return {"data": data, "pagination": pagination(page, page_size, total)}


def get_store(db: Session, cnpj: str, period: DateBounds | None) -> dict | None:
    cnpj = validate_cnpj(cnpj)
    summary = _store_summary_subquery(period)
    row = db.execute(
        select(Store, summary.c.purchase_count, summary.c.payable_amount, summary.c.last_purchase_at)
        .outerjoin(summary, summary.c.store_id == Store.id)
        .where(Store.cnpj == cnpj)
    ).first()
    if row is None:
        return None
    store, count, amount, last_at = row
    return {
        "id": store.id,
        "cnpj": store.cnpj,
        "name": store.name,
        "address": store.address,
        "details_as_of": store.details_as_of,
        "summary": {
            "purchase_count": int(count or 0),
            "payable_amount": money(amount),
            "last_purchase_at": last_at,
        },
    }


def list_processings(
    db: Session,
    *,
    period: DateBounds | None,
    purchase_id: UUID | None,
    state: str | None,
    result: str | None,
    error_code: str | None,
    capture_method: str | None,
    sort: str,
    page: int,
    page_size: int,
) -> dict:
    clauses = _period_clauses(Processing.created_at, period)
    if purchase_id is not None:
        clauses.append(Processing.purchase_id == purchase_id)
    if state is not None:
        clauses.append(Processing.state == state)
    if result is not None:
        clauses.append(Processing.outcome == result)
    if error_code is not None:
        clauses.append(Processing.error_code == error_code)
    if capture_method is not None:
        clauses.append(Processing.capture_method == capture_method)
    count = db.scalar(select(func.count()).select_from(Processing).where(*clauses)) or 0
    order = (
        (Processing.created_at.asc(), Processing.id.asc())
        if sort == "created_at_asc"
        else (Processing.created_at.desc(), Processing.id.asc())
    )
    rows = db.scalars(
        select(Processing)
        .where(*clauses)
        .order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    data = []
    for processing in rows:
        error = None
        if processing.error_code or processing.error_message:
            error = {
                "code": processing.error_code,
                "message": (processing.error_message or "")[:500] or None,
            }
        data.append(
            {
                "processamento_id": processing.id,
                "purchase_id": processing.purchase_id,
                "capture_method": processing.capture_method,
                "state": processing.state,
                "result": processing.outcome,
                "created_at": processing.created_at,
                "finished_at": processing.finished_at,
                "duration_ms": processing.duration_ms,
                "error": error,
            }
        )
    return {"data": data, "pagination": pagination(page, page_size, count)}
