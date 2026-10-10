from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from marketracking.core.config import get_settings
from marketracking.db.session import get_db
from marketracking.schemas.nfce import ProcessingOutcome, ProcessingResult
from marketracking.services.errors import HTTP_STATUS_BY_ERROR
from marketracking.services.processing_service import process_nfce


router = APIRouter(prefix="/v1/nfce", tags=["nfce"])


@router.post("/process", response_model=ProcessingResult)
async def process_nfce_route(
    url: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    image_bytes = None
    image_size_bytes = None
    if image is not None:
        limit = get_settings().image_max_bytes
        image_bytes = await image.read(limit + 1)
        image_size_bytes = image.size if image.size is not None else len(image_bytes)

    result = await run_in_threadpool(
        process_nfce,
        db,
        url=url,
        image=image_bytes,
        image_size_bytes=image_size_bytes,
    )
    status_code = _response_status(result)
    return JSONResponse(
        status_code=status_code,
        content=result.model_dump(mode="json"),
    )


def _response_status(result: ProcessingResult) -> int:
    if result.error is not None:
        return HTTP_STATUS_BY_ERROR[result.error.code]
    if result.result == ProcessingOutcome.CREATED:
        return 201
    return 200
