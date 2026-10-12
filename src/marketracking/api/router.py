from fastapi import APIRouter

from marketracking.api.routes_health import router as health_router
from marketracking.api.routes_dashboards import router as dashboards_router
from marketracking.api.routes_nfce import router as nfce_router
from marketracking.api.routes_processings import router as processings_router
from marketracking.api.routes_purchase_items import router as purchase_items_router
from marketracking.api.routes_purchases import router as purchases_router
from marketracking.api.routes_reports import router as reports_router
from marketracking.api.routes_stores import router as stores_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(nfce_router)
api_router.include_router(processings_router)
api_router.include_router(purchases_router)
api_router.include_router(purchase_items_router)
api_router.include_router(stores_router)
api_router.include_router(reports_router)
api_router.include_router(dashboards_router)
