"""Точка входа воркера фоновых задач.

Здесь брокер связывается с DI-контейнером и импортируются сами задачи.
Запуск:  taskiq worker gamer_shop.worker:broker
         taskiq scheduler gamer_shop.worker:scheduler
"""

from dishka import make_async_container
from dishka.integrations.taskiq import setup_dishka

from gamer_shop.infrastructure.config import Config
from gamer_shop.infrastructure.services import configure_logging
from gamer_shop.infrastructure.taskiq_base.broker import broker, scheduler
from gamer_shop.infrastructure.tasks import delivery as _delivery_tasks  # noqa: F401 — регистрирует задачи
from gamer_shop.infrastructure.tasks import outbox as _outbox_tasks  # noqa: F401 — регистрирует задачи
from gamer_shop.ioc import setup_providers

config = Config()
configure_logging(config.app.log_level, config.app.log_json)

container = make_async_container(*setup_providers(), context={Config: config})
setup_dishka(container=container, broker=broker)

__all__ = ['broker', 'container', 'scheduler']
