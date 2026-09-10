from dishka import AsyncContainer, Provider, Scope, provide

from gamer_shop.application.interfaces import CommandDispatcher, OutboxNotifier
from gamer_shop.infrastructure.tasks import DishkaCommandDispatcher, TaskiqOutboxNotifier


class TaskQueueProvider(Provider):
    @provide(scope=Scope.APP)
    def command_dispatcher(self, container: AsyncContainer) -> CommandDispatcher:
        # Контейнер нужен, чтобы открыть отдельную область видимости на каждую
        # команду: своя сессия и своя транзакция вместо общих на всю пачку.
        return DishkaCommandDispatcher(container)

    @provide(scope=Scope.APP)
    def outbox_notifier(self) -> OutboxNotifier:
        return TaskiqOutboxNotifier()
