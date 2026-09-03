from typing import Protocol

from gamer_shop.application.dto import OrderDTO
from gamer_shop.application.enums import OrderStatus


class OrderRepository(Protocol):
    async def create(self, sku: str, price: int, currency: str) -> OrderDTO: ...

    async def get_by_id(self, order_id: str) -> OrderDTO | None: ...

    async def lock_by_id(self, order_id: str) -> OrderDTO | None:
        """SELECT ... FOR UPDATE — сериализует вебхуки по одному заказу."""
        ...

    async def try_reclaim_stale_delivering(
        self, order_id: str, stale_after_seconds: int
    ) -> bool:
        """Перехватить заказ, застрявший в delivering.

        CAS по давности: выигрывает один воркер, второй уже не найдёт строку
        достаточно старой.
        """
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

    async def find_stale(
        self, statuses: frozenset[OrderStatus], older_than_seconds: int, limit: int
    ) -> list[str]:
        """Кандидаты для фонового дожатия. Право на каждый берётся отдельно."""
        ...
