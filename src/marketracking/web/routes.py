from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from marketracking.core.config import get_settings
from marketracking.services.nfce_service import ReceiptCollectionError, collect_receipt_from_qr_url
from marketracking.services.qr_service import decode_qr_bytes

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

web_router = APIRouter(include_in_schema=False)


def render_home(request: Request, **extra_context: Any) -> HTMLResponse:
    settings = get_settings()
    context = {
        "request": request,
        "app_name": settings.app_name,
        "detected_qr_code": "",
        "qr_status": None,
        "collection_error": None,
        "receipt_data": None,
    }
    context.update(extra_context)
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@web_router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return render_home(request)


@web_router.post("/qr/check", response_class=JSONResponse)
async def check_receipt_qr(receipt_image: UploadFile = File(...)) -> JSONResponse:
    if not receipt_image.content_type or not receipt_image.content_type.startswith("image/"):
        return JSONResponse(
            status_code=400,
            content={
                "found": False,
                "qr_code": None,
                "message": "Anexe um arquivo de imagem valido para buscar o QR Code.",
            },
        )

    image_bytes = await receipt_image.read()
    if not image_bytes:
        return JSONResponse(
            status_code=400,
            content={
                "found": False,
                "qr_code": None,
                "message": "A imagem enviada esta vazia.",
            },
        )

    qr_code = decode_qr_bytes(image_bytes)
    if qr_code:
        return JSONResponse(
            content={
                "found": True,
                "qr_code": qr_code,
                "message": "QR Code encontrado na imagem. Voce ja pode coletar as informacoes.",
            }
        )

    return JSONResponse(
        content={
            "found": False,
            "qr_code": None,
            "message": "Nenhum QR Code foi encontrado nessa imagem.",
        }
    )


@web_router.post("/collect", response_class=HTMLResponse)
def collect_receipt_info(request: Request, qr_code: str = Form(...)) -> HTMLResponse:
    if not qr_code.strip():
        return render_home(
            request,
            qr_status={"tone": "error", "message": "Nenhum QR Code foi enviado para a coleta."},
            collection_error="Anexe uma imagem valida e aguarde a leitura do QR Code.",
        )

    try:
        receipt_data = collect_receipt_from_qr_url(qr_code)
    except ReceiptCollectionError as exc:
        return render_home(
            request,
            detected_qr_code=qr_code,
            qr_status={"tone": "success", "message": "QR Code identificado. A coleta nao conseguiu concluir."},
            collection_error=str(exc),
        )

    return render_home(
        request,
        detected_qr_code=qr_code,
        qr_status={"tone": "success", "message": "QR Code identificado e pronto para consulta."},
        receipt_data=receipt_data,
    )


@web_router.get("/about", response_class=HTMLResponse)
def about(request: Request) -> HTMLResponse:
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
    return templates.TemplateResponse(request=request, name="about.html", context=context)
