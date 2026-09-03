from dishka import Provider, Scope, provide

from gamer_shop.application.interfaces import DeliveryLogger, PaymentsLogger
from gamer_shop.infrastructure.services import delivery_logger, payments_logger


class LoggingProvider(Provider):


    scope = Scope.APP

    @provide
    def get_payments_logger(self) -> PaymentsLogger:
        return payments_logger()

    @provide
    def get_delivery_logger(self) -> DeliveryLogger:
        return delivery_logger()
