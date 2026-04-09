from datetime import UTC, datetime

from fastapi import APIRouter

from marketracking.core.config import get_settings
from marketracking.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.app_env,
        timestamp=datetime.now(UTC),
    )
