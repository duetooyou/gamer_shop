from typing import Protocol

from gamer_shop.application.dto import (
    ItemStatusCountsDTO,
    NewOrderLine,
    OrderDTO,
    OrderItemDTO,
)
from gamer_shop.application.enums import OrderItemStatus, OrderStatus


class OrderRepository(Protocol):
    async def create(self, lines: list[NewOrderLine]) -> tuple[OrderDTO, list[OrderItemDTO]]:
        """Заказ и его позиции одной транзакцией."""
        ...

    async def get_by_id(self, order_id: str) -> OrderDTO | None: ...

    async def lock_by_id(self, order_id: str) -> OrderDTO | None:
        """SELECT ... FOR UPDATE — сериализует вебхуки по одному заказу."""
        ...

    async def try_transition(
        self,
        order_id: str,
        target: OrderStatus,
        allowed_sources: frozenset[OrderStatus],
        failure_reason: str | None = None,
    ) -> bool:
        """Условный переход одним UPDATE. True — статус сменил этот вызов."""
        ...

    async def find_unsettled(self, older_than_seconds: int, limit: int) -> list[str]:
        """Заказы, у которых все позиции терминальны, а расчёта не было.

        Сетка безопасности: закрывать заказ должна команда расчёта, и
        находки здесь означают, что она где-то потерялась.
        """
        ...


class OrderItemRepository(Protocol):
    """Позиция — единица выдачи, возврата и денег."""

    async def get_by_id(self, item_id: str) -> OrderItemDTO | None: ...

    async def list_by_order(self, order_id: str) -> list[OrderItemDTO]: ...

    async def mark_paid(self, order_id: str) -> list[OrderItemDTO]:
        """Перевести позиции заказа из pending в paid. Идемпотентно:
        повтор вебхука вернёт тот же список, но не изменит ничего."""
        ...

    async def try_transition(
        self,
        item_id: str,
        target: OrderItemStatus,
        allowed_sources: frozenset[OrderItemStatus],
        failure_reason: str | None = None,
    ) -> bool: ...

    async def try_reclaim_stale_delivering(
        self, item_id: str, stale_after_seconds: int
    ) -> bool:
        """Перехватить позицию, застрявшую в delivering.

        CAS по давности: выигрывает один воркер, второй уже не найдёт строку
        достаточно старой.
        """
        ...

    async def status_counts(self, order_id: str) -> ItemStatusCountsDTO: ...

    async def find_stale(
        self,
        statuses: frozenset[OrderItemStatus],
        older_than_seconds: int,
        limit: int,
    ) -> list[str]:
        """Кандидаты для фонового дожатия."""
        ...
