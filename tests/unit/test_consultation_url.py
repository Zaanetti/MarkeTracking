from urllib.parse import quote

import pytest

from marketracking.schemas.nfce import ErrorCode
from marketracking.services.consultation_url import (
    is_valid_access_key_check_digit,
    validate_consultation_url,
)
from marketracking.services.errors import ProcessingFailure


def make_access_key(prefix: str = "35") -> str:
    first_43 = prefix + "0" * 41
    weighted_sum = sum(
        int(digit) * (2 + index % 8)
        for index, digit in enumerate(reversed(first_43))
    )
    remainder = weighted_sum % 11
    return first_43 + str(0 if remainder < 2 else 11 - remainder)


def test_validates_sp_url_and_keeps_opaque_query_unchanged(consultation_url: str) -> None:
    validated = validate_consultation_url(consultation_url)

    assert validated.url == consultation_url
    assert validated.access_key == consultation_url.split("p=")[1][:44]
    assert len(validated.fingerprint) == 64
    assert "opaque" not in validated.fingerprint


@pytest.mark.parametrize(
    "url",
    [
        "http://www.nfce.fazenda.sp.gov.br/qrcode?p=3526",
        "https://nfce.fazenda.sp.gov.br/qrcode?p=3526",
        "https://www.nfce.fazenda.sp.gov.br/ConsultaPublica.aspx?p=3526",
        "https://user@www.nfce.fazenda.sp.gov.br/qrcode?p=3526",
        "https://www.nfce.fazenda.sp.gov.br:8443/qrcode?p=3526",
        "https://www.nfce.fazenda.sp.gov.br/qrcode?p=3526#fragment",
        "https://www.nfce.fazenda.sp.gov.br/qrcode?p=3526&x=1",
        "https://www.nfce.fazenda.sp.gov.br/qrcode?p=3526&p=3526",
    ],
)
def test_rejects_unapproved_urls(url: str) -> None:
    with pytest.raises(ProcessingFailure) as error:
        validate_consultation_url(url)

    assert error.value.code == ErrorCode.INVALID_CONSULTATION_URL


def test_rejects_invalid_key_check_digit(consultation_url: str) -> None:
    invalid_key = consultation_url.split("p=")[1][:44][:-1] + "9"
    url = consultation_url.replace(consultation_url.split("p=")[1][:44], invalid_key)

    with pytest.raises(ProcessingFailure) as error:
        validate_consultation_url(url)

    assert error.value.code == ErrorCode.INVALID_ACCESS_KEY


def test_rejects_access_key_without_opaque_qr_suffix(access_key: str) -> None:
    url = f"https://www.nfce.fazenda.sp.gov.br/qrcode?p={access_key}"

    with pytest.raises(ProcessingFailure) as error:
        validate_consultation_url(url)

    assert error.value.code == ErrorCode.INVALID_CONSULTATION_URL


def test_rejects_other_brazilian_states() -> None:
    key = make_access_key("35")
    key = "37" + key[2:-1]
    weighted_sum = sum(
        int(digit) * (2 + index % 8)
        for index, digit in enumerate(reversed(key[:43]))
    )
    remainder = weighted_sum % 11
    key = key[:43] + str(0 if remainder < 2 else 11 - remainder)
    url = (
        "https://www.nfce.fazenda.sp.gov.br/qrcode?p="
        f"{key}{quote('|opaque', safe='')}"
    )

    with pytest.raises(ProcessingFailure) as error:
        validate_consultation_url(url)

    assert error.value.code == ErrorCode.UNSUPPORTED_STATE
    assert is_valid_access_key_check_digit(key)
