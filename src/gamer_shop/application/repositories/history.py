from datetime import datetime
from typing import Protocol

from gamer_shop.application.dto import (
    ItemSnapshotDTO,
    MoneySnapshotDTO,
    OrderEventDTO,
    PeriodTotalsDTO,
)


class OrderHistoryRepository(Protocol):
    """Журнал состояний заказа. Только чтение: пишет его триггер."""

    async def events(self, order_id: str, as_of: datetime) -> list[OrderEventDTO]: ...

    async def order_state_at(
        self, order_id: str, as_of: datetime
    ) -> tuple[str, int, str] | None:
        """Статус, сумма и валюта заказа на момент. None — заказа тогда не было."""
        ...

    async def items_at(self, order_id: str, as_of: datetime) -> list[ItemSnapshotDTO]: ...

    async def counts_between(
        self, period_from: datetime, period_to: datetime
    ) -> tuple[int, int, int, int]:
        """Создано заказов, закрыто заказов, выдано позиций, возвращено позиций."""
        ...


class LedgerHistoryRepository(Protocol):
    """Деньги на момент и за период. Проводки не меняются, поэтому срез
    по времени — это просто фильтр, а не отдельное хранилище."""

    async def money_at(self, order_id: str, as_of: datetime) -> MoneySnapshotDTO: ...

    async def totals_between(
        self, period_from: datetime, period_to: datetime
    ) -> PeriodTotalsDTO: ...
