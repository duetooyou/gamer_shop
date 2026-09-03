"""Структурированные логи.

Два логгера, `payments` и `delivery`, чтобы платёжный и выдачный треки
грепались отдельно.
"""

import logging
import sys
from contextvars import ContextVar

import orjson
import structlog

# Проставляется middleware, попадает в каждую запись автоматически.
correlation_id_var: ContextVar[str | None] = ContextVar('correlation_id', default=None)


def _add_correlation_id(logger, method_name, event_dict):  # noqa: ARG001
    cid = correlation_id_var.get()
    if cid is not None:
        event_dict['correlation_id'] = cid
    return event_dict


def _orjson_dumps(obj, default=None, **_kwargs) -> str:
    return orjson.dumps(obj, default=default).decode()


def configure_logging(level: str = 'INFO', json_output: bool = True) -> None:
    logging.basicConfig(format='%(message)s', stream=sys.stdout, level=level.upper())

    renderer = (
        structlog.processors.JSONRenderer(serializer=_orjson_dumps)
        if json_output
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt='iso', utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def payments_logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger('payments')


def delivery_logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger('delivery')


def app_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
