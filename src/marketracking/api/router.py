from fastapi import APIRouter

from marketracking.api.routes_health import router as health_router
from marketracking.api.routes_nfce import router as nfce_router
from marketracking.api.routes_processings import router as processings_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(nfce_router)
api_router.include_router(processings_router)
