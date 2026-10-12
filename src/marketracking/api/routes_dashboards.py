from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from marketracking.db.session import get_db
from marketracking.schemas.dashboards import (
    ItemMetric,
    ItemsDashboard,
    OverviewDashboard,
    ProcessingsDashboard,
    ProcessingBucketGroup,
)
from marketracking.schemas.reports import PriceChangeDirection, PriceChangesReport
from marketracking.schemas.nfce import CaptureMethod
from marketracking.services.dashboard_service import items_dashboard, overview, processing_dashboard
from marketracking.services.query_common import begin_read_snapshot, resolve_period
from marketracking.services.reporting_service import price_changes


router = APIRouter(prefix="/v1/dashboards", tags=["dashboards"])


@router.get(
    "/overview",
    response_model=OverviewDashboard,
    summary="Get current and previous-period KPIs",
    description=(
        "Returns authorized-purchase KPIs and an adjacent previous period with the same number "
        "of local calendar days. Tax and local-code coverage are null when their denominator is 0."
    ),
)
def dashboard_overview(
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    store_cnpj: str | None = Query(default=None, description="Exactly 14 digits."),
    db: Session = Depends(get_db),
) -> OverviewDashboard:
    try:
        period = resolve_period(date_from, date_to, reporting=True)
        assert period is not None
        begin_read_snapshot(db)
        return overview(db, period, store_cnpj)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc


@router.get(
    "/items",
    response_model=ItemsDashboard,
    summary="Rank observed item identities",
    description=(
        "Ranks store-local item identities from authorized purchase lines. Gross line amount is "
        "the sum of source line totals and does not allocate purchase-wide discounts. Quantities "
        "are never combined across commercial units."
    ),
)
def dashboard_items(
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    store_cnpj: str | None = Query(default=None, description="Exactly 14 digits."),
    q: str | None = Query(default=None, description="Trimmed literal substring, 1 to 120 characters."),
    commercial_unit: str | None = Query(default=None, min_length=1, max_length=32),
    metric: ItemMetric = Query(default=ItemMetric.GROSS_LINE_AMOUNT),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> ItemsDashboard:
    if metric == ItemMetric.QUANTITY and commercial_unit is None:
        raise HTTPException(status_code=422, detail="commercial_unit é obrigatório para metric=quantity.")
    try:
        period = resolve_period(date_from, date_to, reporting=True)
        assert period is not None
        begin_read_snapshot(db)
        return items_dashboard(
            db,
            period=period,
            store_cnpj=store_cnpj,
            q=q,
            commercial_unit=commercial_unit,
            metric=metric.value,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc


@router.get(
    "/price-changes",
    response_model=PriceChangesReport,
    summary="Rank observed local price changes",
    description=(
        "Compares the latest authorized purchase in the period with the immediately previous "
        "authorized observation, which may predate the period. Comparisons use the same store, "
        "item identity and commercial unit; they do not represent inflation or cross-store offers."
    ),
)
def dashboard_price_changes(
    date_from: date | None = Query(default=None, description="Inclusive local issue date."),
    date_to: date | None = Query(default=None, description="Inclusive local issue date."),
    store_cnpj: str | None = Query(default=None, description="Exactly 14 digits."),
    commercial_unit: str | None = Query(default=None, min_length=1, max_length=32),
    q: str | None = Query(default=None, description="Trimmed literal substring, 1 to 120 characters."),
    direction: PriceChangeDirection = Query(default=PriceChangeDirection.ALL),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> PriceChangesReport:
    try:
        period = resolve_period(date_from, date_to, reporting=True)
        assert period is not None
        begin_read_snapshot(db)
        return price_changes(
            db,
            period=period,
            store_cnpj=store_cnpj,
            commercial_unit=commercial_unit,
            q=q,
            direction=direction.value,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc


@router.get(
    "/processings",
    response_model=ProcessingsDashboard,
    summary="Summarize processing executions",
    description=(
        "Filters execution creation dates, not fiscal issue dates. Success is determined from "
        "terminal Processing state and outcome; the success-rate denominator excludes in-progress "
        "runs. Duration percentiles use PostgreSQL percentile_cont over terminal non-null values."
    ),
)
def dashboard_processings(
    date_from: date | None = Query(default=None, description="Inclusive local creation date."),
    date_to: date | None = Query(default=None, description="Inclusive local creation date."),
    capture_method: CaptureMethod | None = None,
    bucket: ProcessingBucketGroup = Query(default=ProcessingBucketGroup.DAY),
    db: Session = Depends(get_db),
) -> ProcessingsDashboard:
    try:
        period = resolve_period(date_from, date_to, reporting=True)
        assert period is not None
        begin_read_snapshot(db)
        return processing_dashboard(
            db,
            period=period,
            capture_method=capture_method.value if capture_method else None,
            bucket=bucket.value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc
