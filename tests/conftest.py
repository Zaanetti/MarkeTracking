from pathlib import Path

import pytest


def make_access_key(prefix: str = "35") -> str:
    first_43 = prefix + "0" * 41
    weighted_sum = sum(
        int(digit) * (2 + index % 8)
        for index, digit in enumerate(reversed(first_43))
    )
    remainder = weighted_sum % 11
    check_digit = 0 if remainder < 2 else 11 - remainder
    return f"{first_43}{check_digit}"


@pytest.fixture
def access_key() -> str:
    return make_access_key()


@pytest.fixture
def consultation_url(access_key: str) -> str:
    return (
        "https://www.nfce.fazenda.sp.gov.br/NFCeConsultaPublica/Paginas/"
        f"ConsultaQRCode.aspx?p={access_key}opaque%7Cconsultation-data"
    )


@pytest.fixture
def sefaz_html(access_key: str) -> str:
    fixture_path = Path(__file__).parent / "fixtures" / "sefaz_sp_valid.html"
    grouped_key = " ".join(access_key[index : index + 4] for index in range(0, 44, 4))
    return fixture_path.read_text(encoding="utf-8").replace("{{ACCESS_KEY}}", grouped_key)
