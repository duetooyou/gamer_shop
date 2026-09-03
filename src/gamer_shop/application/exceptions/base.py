from http import HTTPStatus
from typing import Any


class ApplicationException(Exception):
    """HTTPStatus из stdlib, а не litestar: слой application не должен
    зависеть от веб-фреймворка."""

    error_code: str = 'APP_000'
    message: str = 'Application error'
    http_status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        original_error: Exception | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details or {}
        self.original_error = original_error
        super().__init__(self.message)
