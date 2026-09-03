"""Исключения приложения в HTTP-ответы, без try/except в контроллерах."""

from litestar import Request, Response

from gamer_shop.application.exceptions import ApplicationException
from gamer_shop.infrastructure.services import app_logger

logger = app_logger('http')


def application_exception_handler(
    request: Request, exc: ApplicationException
) -> Response:
    log = logger.bind(
        error_code=exc.error_code,
        http_status=exc.http_status_code,
        path=request.url.path,
        method=request.method,
        details=exc.details,
    )
    if exc.http_status_code >= 500:
        log.error('application_exception', exc_info=exc.original_error or exc)
    else:
        log.warning('application_exception')

    body: dict = {'error_code': exc.error_code, 'message': exc.message}
    if exc.details:
        body['details'] = exc.details
    return Response(content=body, status_code=exc.http_status_code)
