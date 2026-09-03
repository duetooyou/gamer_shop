from typing import Protocol

from gamer_shop.application.dto import OrderProblemDTO, PaymentWebhookDTO


class PaymentEventRepository(Protocol):
    async def insert_if_new(self, event: PaymentWebhookDTO) -> bool:
        """ON CONFLICT DO NOTHING. False — событие уже принято."""
        ...

    async def mark_applied(self, event_id: str) -> None: ...

    async def mark_rejected(self, event_id: str, reason: str) -> None: ...

    async def list_unapplied(self, limit: int) -> list[PaymentWebhookDTO]:
        """Принятые, но не применённые — вебхуки раньше заказа."""
        ...

    async def unapplied_report(self, limit: int) -> list[OrderProblemDTO]: ...
