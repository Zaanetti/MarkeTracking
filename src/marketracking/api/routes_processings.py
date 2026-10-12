from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from marketracking.db.session import get_db
from marketracking.schemas.nfce import CaptureMethod, ErrorCode, ProcessingOutcome, ProcessingState, ProcessingTrace
from marketracking.schemas.queries import Page, ProcessingListItem, ProcessingSort
from marketracking.services.query_common import begin_read_snapshot, resolve_date_bounds
from marketracking.services.query_service import list_processings
from marketracking.services.persistence_service import get_processing_trace


router = APIRouter(prefix="/v1/processings", tags=["processings"])


@router.get(
    "",
    response_model=Page[ProcessingListItem],
    summary="List processing executions",
    description=(
        "Filters execution creation time. Historical image submissions without a stored access "
        "key cannot be linked to a purchase and retain purchase_id=null. Steps are available "
        "only from the processing detail endpoint."
    ),
)
def processings(
    date_from: date | None = Query(default=None, description="Inclusive local creation date."),
    date_to: date | None = Query(default=None, description="Inclusive local creation date."),
    purchase_id: UUID | None = None,
    state: ProcessingState | None = None,
    result: ProcessingOutcome | None = Query(default=None, description="Filters the stored outcome."),
    error_code: ErrorCode | None = None,
    capture_method: CaptureMethod | None = None,
    sort: ProcessingSort = Query(default=ProcessingSort.CREATED_AT_DESC),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Page[ProcessingListItem]:
    try:
        period = resolve_date_bounds(date_from, date_to)
        begin_read_snapshot(db)
        return list_processings(
            db,
            period=period,
            purchase_id=purchase_id,
            state=state.value if state else None,
            result=result.value if result else None,
            error_code=error_code.value if error_code else None,
            capture_method=capture_method.value if capture_method else None,
            sort=sort.value,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc


@router.get("/{processamento_id}", response_model=ProcessingTrace)
def processing_trace(
    processamento_id: UUID,
    db: Session = Depends(get_db),
) -> ProcessingTrace:
    try:
        begin_read_snapshot(db)
        trace = get_processing_trace(db, processamento_id)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponível.") from exc
    if trace is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return trace
