from dishka import Provider, Scope, provide

from gamer_shop.application.interfaces import Clock, UUIDGeneratorInterface
from gamer_shop.infrastructure.services import SystemClock, UUID4Generator


class CommonProvider(Provider):
    """Мелочи без состояния — на весь процесс."""

    scope = Scope.APP

    @provide
    def get_uuid_generator(self) -> UUIDGeneratorInterface:
        return UUID4Generator()

    @provide
    def get_clock(self) -> Clock:
        return SystemClock()
