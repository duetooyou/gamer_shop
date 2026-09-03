from typing import Protocol


class DeliveryScheduler(Protocol):
    """Вызывается только после коммита вебхука: иначе воркер начнёт выдачу
    по заказу, которого в БД ещё нет."""

    async def schedule(self, order_id: str) -> None: ...
