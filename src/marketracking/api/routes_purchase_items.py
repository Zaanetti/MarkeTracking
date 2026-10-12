from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from marketracking.db.session import get_db
from marketracking.schemas.queries import (
    ItemSort,
    Page,
    PurchaseItemListItem,
    PurchaseStatusFilter,
)
from marketracking.services.query_common import begin_read_snapshot, resolve_date_bounds
from marketracking.services.query_service import list_purchase_items


router = APIRouter(prefix="/v1/purchase-items", tags=["purchase-items"])


@router.get(
    "",
    response_model=Page[PurchaseItemListItem],
    summary="Search observed purchase lines",
    description=(
        "Returns source line occurrences, not a canonical product catalog. The default status "
        "includes authorized and retained cancelled purchases. q is a literal, case-insensitive "
        "substring of the stored description."
    ),
)
def purchase_items(
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    purchase_id: UUID | None = None,
    store_cnpj: str | None = Query(default=None, description="Exactly 14 digits."),
    store_product_code: str | None = Query(default=None, max_length=120),
    q: str | None = Query(default=None, description="Trimmed literal substring, 1 to 120 characters."),
    commercial_unit: str | None = Query(default=None, min_length=1, max_length=32),
    status: PurchaseStatusFilter = Query(default=PurchaseStatusFilter.ALL),
    sort: ItemSort = Query(default=ItemSort.ISSUED_AT_DESC),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Page[PurchaseItemListItem]:
    try:
        period = resolve_date_bounds(date_from, date_to)
        begin_read_snapshot(db)
        return list_purchase_items(
            db,
            period=period,
            purchase_id=purchase_id,
            store_cnpj=store_cnpj,
            store_product_code=store_product_code,
            q=q,
            commercial_unit=commercial_unit,
            status=status.value,
            sort=sort.value,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc
