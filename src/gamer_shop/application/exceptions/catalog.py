from http import HTTPStatus

from .base import ApplicationException


class ProductNotFoundException(ApplicationException):
    error_code = 'CATALOG_001'
    message = 'Товар не найден или снят с продажи'
    http_status_code = HTTPStatus.NOT_FOUND
