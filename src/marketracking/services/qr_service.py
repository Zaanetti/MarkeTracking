from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from pyzbar.pyzbar import decode


def build_variants(image: Image.Image) -> list[Image.Image]:
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


def decode_qr_bytes(image_bytes: bytes) -> str | None:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            for variant in build_variants(image.copy()):
                results = decode(variant)
                if results:
                    return results[0].data.decode("utf-8", errors="replace")
    except (UnidentifiedImageError, OSError):
        return None

    return None


def decode_qr_code(image_path: str) -> str | None:
    try:
        with open(image_path, "rb") as file_pointer:
            return decode_qr_bytes(file_pointer.read())
    except OSError:
        return None
