from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from marketracking.schemas.nfce import ErrorCode, ProcessingState
from marketracking.services.errors import ProcessingFailure


SEFAZ_SP_HOST = "www.nfce.fazenda.sp.gov.br"
SEFAZ_SP_PATHS = {
    "/qrcode",
    "/NFCeConsultaPublica/Paginas/ConsultaQRCode.aspx",
}
MAX_CONSULTATION_URL_LENGTH = 2048
ACCESS_KEY_PATTERN = re.compile(r"^\d{44}$")


@dataclass(frozen=True)
class ValidatedConsultationUrl:
    url: str
    access_key: str
    fingerprint: str


def is_valid_access_key_check_digit(access_key: str) -> bool:
    if not ACCESS_KEY_PATTERN.fullmatch(access_key):
        return False

    weighted_sum = sum(
        int(digit) * (2 + index % 8)
        for index, digit in enumerate(reversed(access_key[:43]))
    )
    remainder = weighted_sum % 11
    expected = 0 if remainder < 2 else 11 - remainder
    return int(access_key[-1]) == expected


def validate_consultation_url(value: str) -> ValidatedConsultationUrl:
    if not isinstance(value, str):
        raise _failure(ErrorCode.INVALID_CONSULTATION_URL, "A URL de consulta é inválida.")

    url = value.strip()
    if (
        not url
        or len(url) > MAX_CONSULTATION_URL_LENGTH
        or any(ord(char) < 32 for char in url)
        or "\\" in url
    ):
        raise _failure(ErrorCode.INVALID_CONSULTATION_URL, "A URL de consulta é inválida.")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise _failure(
            ErrorCode.INVALID_CONSULTATION_URL,
            "A URL de consulta é inválida.",
            cause=type(exc).__name__,
        ) from exc

    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != SEFAZ_SP_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or "#" in url
        or parsed.path not in SEFAZ_SP_PATHS
    ):
        raise _failure(ErrorCode.INVALID_CONSULTATION_URL, "A URL não pertence à consulta NFC-e de SP.")

    try:
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
            encoding="utf-8",
            errors="strict",
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise _failure(
            ErrorCode.INVALID_CONSULTATION_URL,
            "A URL de consulta possui parâmetros inválidos.",
            cause=type(exc).__name__,
        ) from exc

    if len(query) != 1 or query[0][0] != "p" or not query[0][1]:
        raise _failure(
            ErrorCode.INVALID_CONSULTATION_URL,
            "A URL deve conter somente um parâmetro de consulta válido.",
        )

    payload = query[0][1]
    if len(payload) <= 44 or not payload[44:].strip():
        raise _failure(
            ErrorCode.INVALID_CONSULTATION_URL,
            "A chave fiscal sozinha não permite consultar o documento; envie a URL completa do QR Code.",
        )
    access_key = payload[:44]
    if not ACCESS_KEY_PATTERN.fullmatch(access_key):
        raise _failure(ErrorCode.INVALID_ACCESS_KEY, "A chave fiscal da URL é inválida.")
    if not access_key.startswith("35"):
        raise _failure(
            ErrorCode.UNSUPPORTED_STATE,
            "Neste momento, são aceitas somente NFC-e emitidas em São Paulo.",
        )
    if not is_valid_access_key_check_digit(access_key):
        raise _failure(ErrorCode.INVALID_ACCESS_KEY, "O dígito verificador da chave fiscal é inválido.")

    fingerprint = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return ValidatedConsultationUrl(url=url, access_key=access_key, fingerprint=fingerprint)


def _failure(
    code: ErrorCode,
    message: str,
    *,
    cause: str | None = None,
) -> ProcessingFailure:
    return ProcessingFailure(
        code=code,
        message=message,
        stage=ProcessingState.VALIDATING_URL,
        technical_cause=cause,
    )
