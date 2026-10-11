from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
import zxingcpp
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from pyzbar.pyzbar import ZBarSymbol, decode

from marketracking.schemas.nfce import ErrorCode, ProcessingState
from marketracking.services.errors import ProcessingFailure


SUPPORTED_FORMATS = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 10_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_RECOVERY_DIMENSION = 1600


@dataclass(frozen=True)
class ImageMetadata:
    media_type: str
    byte_size: int
    width: int
    height: int
    sha256: str


@dataclass(frozen=True)
class QrDecodeResult:
    url: str
    image: ImageMetadata


def build_variants(image: Image.Image) -> list[Image.Image]:
    """Legacy Pillow preprocessing retained for QR reading."""
    gray = ImageOps.grayscale(image)
    contrasted = ImageOps.autocontrast(gray)
    high_contrast = ImageEnhance.Contrast(contrasted).enhance(2.2)
    sharpened = high_contrast.filter(ImageFilter.SHARPEN)
    enlarged = sharpened.resize(
        (sharpened.width * 2, sharpened.height * 2),
        Image.Resampling.LANCZOS,
    )
    return [
        image,
        gray,
        contrasted,
        high_contrast,
        sharpened,
        enlarged,
        enlarged.rotate(90, expand=True),
        enlarged.rotate(180, expand=True),
        enlarged.rotate(270, expand=True),
    ]


def decode_qr_bytes(
    image_bytes: bytes,
    *,
    max_bytes: int = MAX_IMAGE_BYTES,
    max_dimension: int = MAX_IMAGE_DIMENSION,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> QrDecodeResult:
    metadata = inspect_image(
        image_bytes,
        max_bytes=max_bytes,
        max_dimension=max_dimension,
        max_pixels=max_pixels,
    )
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            image.load()
            payloads: set[str] = set()
            multiple_in_variant = False
            for variant in build_variants(image.copy()):
                results = decode(variant, symbols=[ZBarSymbol.QRCODE])
                if len(results) > 1:
                    multiple_in_variant = True
                for result in results:
                    payload = result.data.decode("utf-8", errors="strict").strip()
                    if payload:
                        payloads.add(payload)
            if not payloads:
                fallback_payloads, fallback_multiple = _decode_with_zxing(image)
                payloads.update(fallback_payloads)
                multiple_in_variant = multiple_in_variant or fallback_multiple
            if not payloads:
                recovered_payloads, recovered_multiple = _decode_with_local_thresholds(image)
                payloads.update(recovered_payloads)
                multiple_in_variant = multiple_in_variant or recovered_multiple
    except UnicodeDecodeError as exc:
        raise ProcessingFailure(
            ErrorCode.QR_UNDECODABLE,
            "O QR Code da imagem não contém uma URL de consulta legível.",
            ProcessingState.DECODING_QR,
            technical_cause=type(exc).__name__,
        ) from exc
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ProcessingFailure(
            ErrorCode.INVALID_IMAGE,
            "O arquivo enviado não é uma imagem íntegra e compatível.",
            ProcessingState.PROCESSING_IMAGE,
            technical_cause=type(exc).__name__,
        ) from exc
    except Exception as exc:
        raise ProcessingFailure(
            ErrorCode.QR_UNDECODABLE,
            "Não foi possível decodificar o QR Code da imagem.",
            ProcessingState.DECODING_QR,
            technical_cause=type(exc).__name__,
        ) from exc

    if multiple_in_variant or len(payloads) > 1:
        raise ProcessingFailure(
            ErrorCode.MULTIPLE_QR_CODES,
            "A imagem contém mais de um QR Code de consulta.",
            ProcessingState.DECODING_QR,
        )
    if not payloads:
        raise ProcessingFailure(
            ErrorCode.QR_NOT_FOUND,
            "Nenhum QR Code de consulta foi encontrado na imagem.",
            ProcessingState.DECODING_QR,
        )
    return QrDecodeResult(url=next(iter(payloads)), image=metadata)


def _decode_with_zxing(image: Image.Image) -> tuple[set[str], bool]:
    payloads: set[str] = set()
    multiple = False
    for rotation in (0, 90, 180, 270):
        oriented = image.rotate(rotation, expand=True) if rotation else image
        gray = ImageOps.autocontrast(ImageOps.grayscale(oriented))
        variants = (
            oriented.convert("RGB"),
            gray,
            gray.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=3)),
            ImageEnhance.Contrast(gray).enhance(2.5),
        )
        for variant in variants:
            enlarged = variant.resize(
                (variant.width * 2, variant.height * 2),
                Image.Resampling.LANCZOS,
            )
            for candidate in (variant, enlarged):
                for binarizer in (
                    zxingcpp.Binarizer.LocalAverage,
                    zxingcpp.Binarizer.GlobalHistogram,
                ):
                    results = zxingcpp.read_barcodes(
                        candidate,
                        formats=zxingcpp.BarcodeFormat.QRCode,
                        binarizer=binarizer,
                    )
                    if len(results) > 1:
                        multiple = True
                    for result in results:
                        payload = result.text.strip()
                        if payload:
                            payloads.add(payload)
    return payloads, multiple


def _decode_with_local_thresholds(image: Image.Image) -> tuple[set[str], bool]:
    """Recover fragmented thermal-print modules under uneven illumination."""
    reduced = image.convert("RGB")
    reduced.thumbnail(
        (MAX_RECOVERY_DIMENSION, MAX_RECOVERY_DIMENSION), Image.Resampling.LANCZOS
    )
    gray = cv2.cvtColor(np.asarray(reduced), cv2.COLOR_RGB2GRAY)
    kernel = np.ones((2, 2), dtype=np.uint8)
    sources = (gray, cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray))
    payloads: set[str] = set()
    multiple = False
    for source in sources:
        _, otsu = cv2.threshold(source, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        thresholds = (otsu,) + tuple(
            cv2.adaptiveThreshold(
                source, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 2
            )
            for block in (31, 55, 81)
        )
        for binary in thresholds:
            # White-mask erosion fills small gaps in the dark printed modules.
            candidates = (
                binary,
                cv2.erode(binary, kernel),
                cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel),
            )
            for candidate in candidates:
                results = decode(candidate, symbols=[ZBarSymbol.QRCODE])
                multiple = multiple or len(results) > 1
                for result in results:
                    payload = result.data.decode("utf-8", errors="strict").strip()
                    if payload:
                        payloads.add(payload)
    return payloads, multiple


def inspect_image(
    image_bytes: bytes,
    *,
    max_bytes: int = MAX_IMAGE_BYTES,
    max_dimension: int = MAX_IMAGE_DIMENSION,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> ImageMetadata:
    if not image_bytes or len(image_bytes) > max_bytes:
        raise ProcessingFailure(
            ErrorCode.INVALID_IMAGE,
            "A imagem está vazia ou excede o tamanho permitido.",
            ProcessingState.PROCESSING_IMAGE,
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_bytes)) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in SUPPORTED_FORMATS:
                    raise ProcessingFailure(
                        ErrorCode.INVALID_IMAGE,
                        "Formato de imagem não suportado. Envie PNG, JPEG ou WebP.",
                        ProcessingState.PROCESSING_IMAGE,
                    )
                if (
                    width < 1
                    or height < 1
                    or width > max_dimension
                    or height > max_dimension
                    or width * height > max_pixels
                ):
                    raise ProcessingFailure(
                        ErrorCode.INVALID_IMAGE,
                        "As dimensões da imagem excedem os limites permitidos.",
                        ProcessingState.PROCESSING_IMAGE,
                    )
                image.verify()
    except ProcessingFailure:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ProcessingFailure(
            ErrorCode.INVALID_IMAGE,
            "O arquivo enviado não é uma imagem íntegra e compatível.",
            ProcessingState.PROCESSING_IMAGE,
            technical_cause=type(exc).__name__,
        ) from exc

    return ImageMetadata(
        media_type=SUPPORTED_FORMATS[image_format],
        byte_size=len(image_bytes),
        width=width,
        height=height,
        sha256=hashlib.sha256(image_bytes).hexdigest(),
    )
