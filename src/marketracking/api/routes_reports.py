from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from marketracking.db.session import get_db
from marketracking.schemas.reports import (
    PriceBucketGroup,
    SpendingGroup,
    SpendingReport,
)
from marketracking.schemas.queries import PriceReport
from marketracking.services.query_common import begin_read_snapshot, resolve_period
from marketracking.services.reporting_service import price_report, spending_report


router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.get(
    "/spending",
    response_model=SpendingReport,
    summary="Aggregate authorized purchase spending",
    description=(
        "Returns financial summaries from authorized purchases only. Temporal buckets use "
        "America/Sao_Paulo and include empty buckets with zero totals. Store shares are based on "
        "unrounded BRL values; cancelled purchases are excluded."
    ),
)
def spending(
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    store_cnpj: str | None = Query(default=None, description="Exactly 14 digits."),
    group_by: SpendingGroup = Query(default=SpendingGroup.DAY),
    limit: int = Query(default=10, ge=1, le=50, description="Store group limit; ignored otherwise."),
    db: Session = Depends(get_db),
) -> SpendingReport:
    try:
        period = resolve_period(date_from, date_to, reporting=True)
        assert period is not None
        begin_read_snapshot(db)
        return spending_report(
            db,
            period=period,
            store_cnpj=store_cnpj,
            group_by=group_by.value,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc


@router.get(
    "/prices",
    response_model=PriceReport,
    summary="Report observed prices for one local item identity",
    description=(
        "The commercial unit and one exact store-local identity are required. A source description "
        "matches only lines without a store product code. Purchase observations are quantity "
        "weighted within each purchase; temporal buckets with no observation have a null price."
    ),
)
def prices(
    store_cnpj: str = Query(..., description="Exactly 14 digits."),
    commercial_unit: str = Query(..., min_length=1, max_length=32),
    store_product_code: str | None = Query(default=None, max_length=120),
    description: str | None = Query(default=None, min_length=1, max_length=512),
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    bucket: PriceBucketGroup = Query(default=PriceBucketGroup.PURCHASE),
    page: int | None = Query(default=None, ge=1, description="Available for bucket=purchase."),
    page_size: int | None = Query(
        default=None, ge=1, le=100, description="Available for bucket=purchase."
    ),
    db: Session = Depends(get_db),
) -> PriceReport:
    if (store_product_code is None) == (description is None):
        raise HTTPException(
            status_code=422,
            detail="Informe exatamente um entre store_product_code e description.",
        )
    if bucket != PriceBucketGroup.PURCHASE and (page is not None or page_size is not None):
        raise HTTPException(
            status_code=422,
            detail="Paginação só é aceita quando bucket=purchase.",
        )
    try:
        period = resolve_period(date_from, date_to, reporting=True)
        assert period is not None
        begin_read_snapshot(db)
        return price_report(
            db,
            period=period,
            store_cnpj=store_cnpj,
            commercial_unit=commercial_unit,
            store_product_code=store_product_code,
            description=description,
            bucket=bucket.value,
            page=page or 1,
            page_size=page_size or 25,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc
