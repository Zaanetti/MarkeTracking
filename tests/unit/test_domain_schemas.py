from datetime import datetime

import pytest
from pydantic import ValidationError

from marketracking.schemas.nfce import StoreData


def test_store_details_timestamp_must_include_timezone() -> None:
    with pytest.raises(ValidationError):
        StoreData(
            cnpj="12345678000195",
            name="Mercado",
            address="Rua Exemplo, 1",
            details_as_of=datetime(2026, 10, 10, 12, 0),
        )
