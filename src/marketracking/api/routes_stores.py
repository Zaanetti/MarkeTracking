from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from marketracking.db.session import get_db
from marketracking.schemas.queries import Page, StoreDetail, StoreListItem, StoreSort
from marketracking.services.query_common import begin_read_snapshot, resolve_date_bounds
from marketracking.services.query_service import get_store, list_stores


router = APIRouter(prefix="/v1/stores", tags=["stores"])


@router.get(
    "",
    response_model=Page[StoreListItem],
    summary="List stores",
    description=(
        "Returns current Store details and an authorized-purchase summary for the selected "
        "issue-date period. Stores with no purchases in the period remain in the result."
    ),
)
def stores(
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    q: str | None = Query(default=None, description="Trimmed literal substring, 1 to 120 characters."),
    cnpj: str | None = Query(default=None, description="Exactly 14 digits."),
    sort: StoreSort = Query(default=StoreSort.NAME_ASC),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Page[StoreListItem]:
    try:
        period = resolve_date_bounds(date_from, date_to)
        begin_read_snapshot(db)
        return list_stores(
            db,
            period=period,
            q=q,
            cnpj=cnpj,
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
    "/{cnpj}",
    response_model=StoreDetail,
    summary="Get a store",
    description="A known store returns a zero summary when it has no authorized purchases in period.",
)
def store_detail(
    cnpj: str,
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    db: Session = Depends(get_db),
) -> StoreDetail:
    try:
        period = resolve_date_bounds(date_from, date_to)
        begin_read_snapshot(db)
        result = get_store(db, cnpj, period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Estabelecimento não encontrado.")
    return result
