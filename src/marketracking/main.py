from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from marketracking.api.router import api_router
from marketracking.core.config import get_settings
from marketracking.core.middleware import (
    BODY_TOO_LARGE_MESSAGE,
    RequestBodyLimitMiddleware,
    RequestBodyTooLarge,
)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=settings.project_description,
    debug=settings.app_debug,
    version="0.1.0",
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=settings.image_max_bytes + 256 * 1024,
)


@app.exception_handler(RequestBodyTooLarge)
async def request_body_too_large_handler(
    _request: Request,
    _exc: RequestBodyTooLarge,
) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": BODY_TOO_LARGE_MESSAGE})

app.include_router(api_router, prefix="/api")
