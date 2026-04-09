from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from marketracking.api.router import api_router
from marketracking.core.config import get_settings
from marketracking.web.routes import web_router

BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=settings.project_description,
    debug=settings.app_debug,
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(web_router)
app.include_router(api_router, prefix="/api")
