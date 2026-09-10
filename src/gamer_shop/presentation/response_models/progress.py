from .base import ResponseBase


class RateLimitOut(ResponseBase):
    supplier: str
    capacity: int
    # Наш темп: объявленный лимит поставщика минус запас, с которым мы
    # идём заведомо ниже него.
    pace_per_minute: int
    # Сколько обращений можно сделать прямо сейчас.
    available: float
    granted: int
    # Сколько раз очередь упёрлась в лимит. Растёт при всплеске — и это
    # нормально: значит лимит соблюдается, а заказы ждут, а не теряются.
    throttled: int


class SupplierQueueOut(ResponseBase):
    supplier: str
    queued: int
    ready: int
    in_flight: int
    oldest_wait_seconds: float | None = None


class ProgressOut(ResponseBase):
    orders_total: int
    orders_in_queue: int
    orders_settled: int
    items_total: int
    items_in_queue: int
    items_delivered: int
    items_refunded: int
    queue: list[SupplierQueueOut]
    rate_limits: list[RateLimitOut]
    dead_commands: int
