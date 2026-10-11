from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
import zxingcpp
from PIL import Image

from marketracking.schemas.nfce import ErrorCode
from marketracking.services.errors import ProcessingFailure
from marketracking.services.qr_service import decode_qr_bytes


def png_bytes(image: Image.Image | None = None) -> bytes:
    stream = BytesIO()
    if image is None:
        image = Image.new("RGB", (48, 32), "white")
    image.save(stream, format="PNG")
    return stream.getvalue()


def faded_qr_image(payload: str) -> Image.Image:
    barcode = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.QRCode)
    bitmap = np.asarray(zxingcpp.write_barcode_to_image(barcode, scale=7))
    rng = np.random.default_rng(17)
    ink = bitmap < 128
    holes = ink & (rng.random(bitmap.shape) < 0.20)
    reflectance = np.where(ink & ~holes, 95, 225).astype(np.float32)
    shadow = np.linspace(0.30, 1, bitmap.shape[1], dtype=np.float32)
    faded = np.clip(
        reflectance * shadow[None, :] + rng.normal(0, 3, bitmap.shape), 0, 255
    ).astype(np.uint8)
    return Image.fromarray(faded)


def test_rejects_invalid_image_content() -> None:
    with pytest.raises(ProcessingFailure) as error:
        decode_qr_bytes(b"not an image")

    assert error.value.code == ErrorCode.INVALID_IMAGE


def test_rejects_image_over_size_limit() -> None:
    with pytest.raises(ProcessingFailure) as error:
        decode_qr_bytes(png_bytes(), max_bytes=1)

    assert error.value.code == ErrorCode.INVALID_IMAGE


def test_reports_image_without_qr(monkeypatch) -> None:
    monkeypatch.setattr("marketracking.services.qr_service.decode", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "marketracking.services.qr_service.zxingcpp.read_barcodes",
        lambda *_args, **_kwargs: [],
    )

    with pytest.raises(ProcessingFailure) as error:
        decode_qr_bytes(png_bytes())

    assert error.value.code == ErrorCode.QR_NOT_FOUND


def test_returns_one_qr_payload_and_safe_image_metadata(monkeypatch) -> None:
    payload = "https://www.nfce.fazenda.sp.gov.br/qrcode?p=opaque"
    monkeypatch.setattr(
        "marketracking.services.qr_service.decode",
        lambda *_args, **_kwargs: [SimpleNamespace(data=payload.encode())],
    )

    result = decode_qr_bytes(png_bytes())

    assert result.url == payload
    assert result.image.media_type == "image/png"
    assert result.image.width == 48
    assert result.image.height == 32
    assert len(result.image.sha256) == 64


def test_uses_zxing_when_legacy_decoder_finds_no_code(monkeypatch) -> None:
    payload = "https://www.nfce.fazenda.sp.gov.br/qrcode?p=opaque"
    monkeypatch.setattr("marketracking.services.qr_service.decode", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "marketracking.services.qr_service.zxingcpp.read_barcodes",
        lambda *_args, **_kwargs: [SimpleNamespace(text=payload)],
    )

    result = decode_qr_bytes(png_bytes())

    assert result.url == payload


def test_uses_upscaled_rotated_image_with_zxing(monkeypatch) -> None:
    payload = "https://www.nfce.fazenda.sp.gov.br/qrcode?p=opaque"
    monkeypatch.setattr("marketracking.services.qr_service.decode", lambda *_args, **_kwargs: [])

    def decode_rotated(image, **_kwargs):
        if image.size == (64, 96):
            return [SimpleNamespace(text=payload)]
        return []

    monkeypatch.setattr(
        "marketracking.services.qr_service.zxingcpp.read_barcodes",
        decode_rotated,
    )

    result = decode_qr_bytes(png_bytes())

    assert result.url == payload


def test_rejects_multiple_qrs_found_by_zxing(monkeypatch) -> None:
    symbols = [SimpleNamespace(text="first"), SimpleNamespace(text="second")]
    monkeypatch.setattr("marketracking.services.qr_service.decode", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "marketracking.services.qr_service.zxingcpp.read_barcodes",
        lambda *_args, **_kwargs: symbols,
    )

    with pytest.raises(ProcessingFailure) as error:
        decode_qr_bytes(png_bytes())

    assert error.value.code == ErrorCode.MULTIPLE_QR_CODES


def test_recovers_fragmented_qr_under_uneven_lighting(monkeypatch) -> None:
    payload = "https://www.nfce.fazenda.sp.gov.br/qrcode?p=synthetic-thermal-print"
    image = faded_qr_image(payload)
    monkeypatch.setattr("marketracking.services.qr_service.build_variants", lambda _image: [])
    monkeypatch.setattr(
        "marketracking.services.qr_service._decode_with_zxing", lambda _image: (set(), False)
    )

    result = decode_qr_bytes(png_bytes(image))

    assert result.url == payload
    assert (result.image.width, result.image.height) == image.size


def test_rejects_multiple_qrs_recovered_from_faded_image(monkeypatch) -> None:
    first = faded_qr_image("https://www.nfce.fazenda.sp.gov.br/qrcode?p=synthetic-first")
    second = faded_qr_image("https://www.nfce.fazenda.sp.gov.br/qrcode?p=synthetic-second")
    combined = Image.new("L", (first.width + second.width + 32, max(first.height, second.height)), 255)
    combined.paste(first, (0, 0))
    combined.paste(second, (first.width + 32, 0))
    monkeypatch.setattr("marketracking.services.qr_service.build_variants", lambda _image: [])
    monkeypatch.setattr(
        "marketracking.services.qr_service._decode_with_zxing", lambda _image: (set(), False)
    )

    with pytest.raises(ProcessingFailure) as error:
        decode_qr_bytes(png_bytes(combined))

    assert error.value.code == ErrorCode.MULTIPLE_QR_CODES


def test_rejects_multiple_qrs(monkeypatch) -> None:
    symbols = [SimpleNamespace(data=b"first"), SimpleNamespace(data=b"second")]
    monkeypatch.setattr(
        "marketracking.services.qr_service.decode",
        lambda *_args, **_kwargs: symbols,
    )

    with pytest.raises(ProcessingFailure) as error:
        decode_qr_bytes(png_bytes())

    assert error.value.code == ErrorCode.MULTIPLE_QR_CODES
