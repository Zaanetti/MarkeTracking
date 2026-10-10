from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from marketracking.db.session import get_db
from marketracking.schemas.nfce import ProcessingTrace
from marketracking.services.persistence_service import get_processing_trace


router = APIRouter(prefix="/v1/processings", tags=["processings"])


@router.get("/{processamento_id}", response_model=ProcessingTrace)
def processing_trace(
    processamento_id: UUID,
    db: Session = Depends(get_db),
) -> ProcessingTrace:
    trace = get_processing_trace(db, processamento_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return trace
