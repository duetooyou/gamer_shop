from typing import Protocol

from gamer_shop.application.dto import OrderProblemDTO


class ReconciliationRepository(Protocol):
    """Запросы сверки. Вынесены отдельно: это отчётность, а не операции."""

    async def paid_not_delivered(self, older_than_seconds: int, limit: int) -> list[OrderProblemDTO]: ...

    async def delivered_not_paid(self, limit: int) -> list[OrderProblemDTO]: ...

    async def stuck_delivering(self, older_than_seconds: int, limit: int) -> list[OrderProblemDTO]: ...

    async def stock_drift(self) -> list[str]: ...
