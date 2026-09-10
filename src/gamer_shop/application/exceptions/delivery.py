from http import HTTPStatus

from .base import ApplicationException


class DeliveryNotFinishedException(ApplicationException):
    """Позиция не дошла до конца, но и провалом это не считается.

    Бросается наружу намеренно: повтором команды заведует релей, и только он
    знает про бэкофф, лимит поставщика и число попыток. Возвращать сюда
    «успешно, но повторите» значило бы завести второй механизм повторов
    рядом с уже имеющимся.
    """

    error_code = 'DELIVERY_001'
    message = 'Выдача не завершена, требуется повтор'
    http_status_code = HTTPStatus.CONFLICT


class SupplierThrottledException(DeliveryNotFinishedException):
    """Лимит поставщика исчерпан: обращаться сейчас нельзя.

    Не провал выдачи, а её отсрочка. Отдельный класс нужен релею: команду
    надо вернуть в очередь на срок до следующего разрешения и не считать
    это попыткой — иначе всплеск сам себя добьёт до исчерпания попыток.
    """

    error_code = 'DELIVERY_002'
    message = 'Лимит поставщика исчерпан, обращение отложено'

    def __init__(
        self,
        message: str | None = None,
        details: dict | None = None,
        retry_after_seconds: float = 1.0,
    ) -> None:
        super().__init__(message, details)
        self.retry_after_seconds = retry_after_seconds
