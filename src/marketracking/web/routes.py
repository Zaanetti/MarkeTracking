from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from marketracking.core.config import get_settings

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

web_router = APIRouter(include_in_schema=False)


@web_router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    settings = get_settings()
    context = {
        "request": request,
        "app_name": settings.app_name,
        "environment": settings.app_env,
        "stack_items": [
            "FastAPI para web e API",
            "PostgreSQL para persistencia",
            "MinIO como storage compativel com S3",
            "Estrutura pronta para parsers e worker",
        ],
    }
    return templates.TemplateResponse(request=request, name="index.html", context=context)
