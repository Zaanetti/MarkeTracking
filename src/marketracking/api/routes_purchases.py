from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from marketracking.db.session import get_db
from marketracking.schemas.queries import (
    Page,
    PurchaseDetail,
    PurchaseListItem,
    PurchaseSort,
    PurchaseStatusFilter,
)
from marketracking.services.query_common import begin_read_snapshot, resolve_date_bounds
from marketracking.services.query_service import get_purchase, list_purchases


router = APIRouter(prefix="/v1/purchases", tags=["purchases"])


@router.get(
    "",
    response_model=Page[PurchaseListItem],
    summary="List purchases",
    description=(
        "Lists persisted purchases without item payloads. Dates filter NFC-e issue time in "
        "America/Sao_Paulo. The default status is all, including retained cancelled purchases."
    ),
)
def purchases(
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    store_cnpj: str | None = Query(default=None, description="Exactly 14 digits."),
    access_key: str | None = Query(default=None, description="Validated 44-digit NFC-e key."),
    status: PurchaseStatusFilter = Query(default=PurchaseStatusFilter.ALL),
    min_total: Decimal | None = Query(default=None, ge=0),
    max_total: Decimal | None = Query(default=None, ge=0),
    sort: PurchaseSort = Query(default=PurchaseSort.ISSUED_AT_DESC),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Page[PurchaseListItem]:
    try:
        period = resolve_date_bounds(date_from, date_to)
        begin_read_snapshot(db)
        return list_purchases(
            db,
            period=period,
            store_cnpj=store_cnpj,
            access_key=access_key,
            status=status.value,
            min_total=min_total,
            max_total=max_total,
            sort=sort.value,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc


@router.get(
    "/{purchase_id}",
    response_model=PurchaseDetail,
    summary="Get a purchase",
    description=(
        "Returns normalized fiscal document data, current Store details, the historical issued "
        "store snapshot, purchase lines and totals. Consumer, payment, URL and image data are "
        "excluded."
    ),
)
def purchase_detail(purchase_id: UUID, db: Session = Depends(get_db)) -> PurchaseDetail:
    try:
        begin_read_snapshot(db)
        result = get_purchase(db, purchase_id)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Compra não encontrada.")
    return result
