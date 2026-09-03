from dishka import Provider, Scope, provide

from gamer_shop.application.interfaces import DeliveryScheduler
from gamer_shop.infrastructure.tasks import TaskiqDeliveryScheduler


class TaskQueueProvider(Provider):
    @provide(scope=Scope.APP)
    def delivery_scheduler(self) -> DeliveryScheduler:
        return TaskiqDeliveryScheduler()
