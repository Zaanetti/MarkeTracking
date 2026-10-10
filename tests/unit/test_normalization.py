from decimal import Decimal

import pytest

from marketracking.schemas.nfce import ErrorCode
from marketracking.services.errors import ProcessingFailure
from marketracking.services.normalization_service import normalize_nfce
from marketracking.parsers.sources.sefaz_sp import SefazSpNfceParser


def test_normalizes_purchase_data_and_validates_totals(sefaz_html: str, access_key: str) -> None:
    raw = SefazSpNfceParser().parse(sefaz_html)

    data = normalize_nfce(raw, access_key)

    assert data.fiscal_document.access_key == access_key
    assert data.store.cnpj == "12345678000195"
    assert data.items[0].quantity == Decimal("1.0000")
    assert data.items[0].unit_price == Decimal("10.00")
    assert data.items[0].line_total == Decimal("10.00")
    assert data.total.payable_amount == Decimal("10.00")
    assert data.fiscal_document.issued_at.tzinfo is not None


def test_rejects_access_key_mismatch(sefaz_html: str, access_key: str) -> None:
    raw = SefazSpNfceParser().parse(sefaz_html)
    wrong_key = "35" + "0" * 41

    with pytest.raises(ProcessingFailure) as error:
        normalize_nfce(raw, wrong_key)

    assert error.value.code == ErrorCode.ACCESS_KEY_MISMATCH


def test_rejects_monetary_inconsistency(sefaz_html: str, access_key: str) -> None:
    raw = SefazSpNfceParser().parse(sefaz_html)
    raw = raw.model_copy(update={"gross_items_amount": "11,00"})

    with pytest.raises(ProcessingFailure) as error:
        normalize_nfce(raw, access_key)

    assert error.value.code == ErrorCode.MONETARY_INCONSISTENCY


def test_rejects_declared_line_count_mismatch(sefaz_html: str, access_key: str) -> None:
    raw = SefazSpNfceParser().parse(sefaz_html)
    raw = raw.model_copy(update={"declared_line_count": "2"})

    with pytest.raises(ProcessingFailure) as error:
        normalize_nfce(raw, access_key)

    assert error.value.code == ErrorCode.MONETARY_INCONSISTENCY
