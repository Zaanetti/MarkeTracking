from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from marketracking.schemas.nfce import ErrorCode
from marketracking.services.errors import ProcessingFailure
from marketracking.services.qr_service import decode_qr_bytes


def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (48, 32), "white").save(stream, format="PNG")
    return stream.getvalue()


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


def test_rejects_multiple_qrs(monkeypatch) -> None:
    symbols = [SimpleNamespace(data=b"first"), SimpleNamespace(data=b"second")]
    monkeypatch.setattr(
        "marketracking.services.qr_service.decode",
        lambda *_args, **_kwargs: symbols,
    )

    with pytest.raises(ProcessingFailure) as error:
        decode_qr_bytes(png_bytes())

    assert error.value.code == ErrorCode.MULTIPLE_QR_CODES
