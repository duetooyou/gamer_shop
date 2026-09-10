from gamer_shop.application.interactors import OutboxRelayInteractor
from gamer_shop.infrastructure.tasks.outbox import outbox_relay_task


class TaskiqOutboxNotifier:
    """Побудка релея через очередь задач.

    Ошибка здесь не страшна: команда уже лежит в таблице, её подберёт
    плановый проход. Поэтому побудка и не входит в транзакцию.
    """

    async def notify(self) -> None:
        await outbox_relay_task.kiq()


class InlineOutboxNotifier:
    """Релей прямо в текущем процессе.

    Для тестов: даёт сквозной путь без воркера и заодно самую жёсткую гонку —
    параллельные запросы разбирают очередь внутри обработчика.

    Крутится до тишины, потому что команда порождает команду: выдача просит
    расчёт заказа, отказ — возврат. В бою эту цепочку разматывает следующий
    проход релея, здесь ждать его негде.
    """

    MAX_PASSES = 5

    def __init__(self, relay: OutboxRelayInteractor) -> None:
        self._relay = relay

    async def notify(self) -> None:
        for _ in range(self.MAX_PASSES):
            if await self._relay.run_once() == 0:
                return
