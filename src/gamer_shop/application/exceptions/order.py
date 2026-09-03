from http import HTTPStatus

from .base import ApplicationException


class OrderNotFoundException(ApplicationException):
    error_code = 'ORDER_001'
    message = 'Заказ не найден'
    http_status_code = HTTPStatus.NOT_FOUND


class InvalidWebhookPayloadException(ApplicationException):
    error_code = 'ORDER_002'
    message = 'Некорректная полезная нагрузка вебхука'
    http_status_code = HTTPStatus.BAD_REQUEST
