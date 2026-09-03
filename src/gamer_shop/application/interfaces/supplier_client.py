from typing import Protocol

from gamer_shop.application.dto import SupplierOutcome
from gamer_shop.application.enums import SupplierName


class SupplierClient(Protocol):
    """Клиент к поставщику.

    Держит таймаут, повторяет тем же request_id, возвращает один из трёх
    исходов. Решение о фолбэке принимает интерактор, а не клиент.
    """

    async def issue(
        self, supplier: SupplierName, request_id: str, sku: str, order_id: str
    ) -> SupplierOutcome: ...
