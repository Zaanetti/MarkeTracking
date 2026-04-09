from abc import ABC, abstractmethod
from typing import Any


class BaseReceiptParser(ABC):
    name = "base"

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Indica se o parser reconhece a origem do documento."""

    @abstractmethod
    def parse(self, html: str) -> dict[str, Any]:
        """Extrai o payload bruto de uma pagina de cupom."""
