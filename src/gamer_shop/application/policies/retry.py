"""Бэкофф между повторами к поставщику."""

import random


def backoff_delay(attempt: int, base: float, maximum: float, jitter: float) -> float:
    """Экспоненциальная задержка перед попыткой (нумерация с 1).

    Джиттер обязателен, иначе пачка заказов будет бить в поставщика синхронно.
    """
    delay = min(base * (2 ** max(attempt - 1, 0)), maximum)
    return delay + random.uniform(0, jitter)
