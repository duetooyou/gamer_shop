"""Брокер фоновых задач.

Выдача вынесена из HTTP-запроса: вебхуку нужен быстрый 200, а поход
к поставщику занимает секунды. Про DI-контейнер модуль намеренно не знает —
его подключает точка входа воркера, иначе получился бы цикл импорта.
"""

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from gamer_shop.infrastructure.config import Config

_config = Config()

broker = RedisStreamBroker(url=_config.redis.url()).with_result_backend(
    RedisAsyncResultBackend(redis_url=_config.redis.url(), result_ex_time=3600)
)

scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
