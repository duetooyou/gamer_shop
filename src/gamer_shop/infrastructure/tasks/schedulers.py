from gamer_shop.application.interactors import DeliverOrderInteractor
from gamer_shop.infrastructure.tasks.delivery import deliver_order_task


class TaskiqDeliveryScheduler:
    async def schedule(self, order_id: str) -> None:
        await deliver_order_task.kiq(order_id)


class InlineDeliveryScheduler:
    """Выдача сразу в текущем процессе.

    Для тестов: даёт сквозной путь без воркера и заодно самую жёсткую гонку —
    параллельные запросы выдают один заказ прямо внутри обработчика.
    """

    def __init__(self, interactor: DeliverOrderInteractor) -> None:
        self._interactor = interactor

    async def schedule(self, order_id: str) -> None:
        await self._interactor.execute(order_id)
