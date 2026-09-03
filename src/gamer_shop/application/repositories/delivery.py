from typing import Protocol

from gamer_shop.application.dto import (
    DeliveryDTO,
    OrderProblemDTO,
    SupplierOutcome,
    SupplierRequestSnapshot,
)
from gamer_shop.application.enums import SupplierName, SupplierRequestState


class DeliveryRepository(Protocol):
    async def create_if_absent(
        self, order_id: str, code: str, supplier: str, request_id: str
    ) -> bool:
        """Записать выдачу. False — выдача по заказу или этот код уже есть."""
        ...

    async def get_by_order(self, order_id: str) -> DeliveryDTO | None: ...

    async def code_taken_by_other_order(self, code: str, order_id: str) -> bool:
        """Закреплён ли код за другим заказом.

        Нужна до фиксации: позволяет уйти к резервному поставщику
        в том же проходе.
        """
        ...


class SupplierRequestRepository(Protocol):
    """По одному обращению на пару (заказ, поставщик)."""

    async def get_or_create(
        self, order_id: str, supplier: SupplierName, sku: str, request_id: str
    ) -> SupplierRequestSnapshot:
        """Известное состояние обращения либо новая строка в pending."""
        ...

    async def save_outcome(self, outcome: SupplierOutcome) -> None: ...

    async def unresolved(self, limit: int) -> list[OrderProblemDTO]:
        """Обращения в состоянии unknown."""
        ...

    async def state_of(
        self, order_id: str, supplier: SupplierName
    ) -> SupplierRequestState | None: ...
