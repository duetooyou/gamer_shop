from dataclasses import dataclass, field

from gamer_shop.application.dto.rate_limit import RateLimitStateDTO


@dataclass(frozen=True, slots=True)
class SupplierQueueDTO:
    """Очередь к одному поставщику."""

    supplier: str
    queued: int
    # Из них доступны прямо сейчас: остальные ждут либо бэкоффа, либо
    # свободного места в лимите.
    ready: int
    # Позиции, по которым обращение уже в пути.
    in_flight: int
    oldest_wait_seconds: float | None


@dataclass(frozen=True, slots=True)
class ProgressDTO:
    """Сколько заказов в очереди и сколько уже выдано.

    Считается по состоянию, а не по счётчикам в памяти: перезапуск процесса
    не обнуляет картину.
    """

    orders_total: int = 0
    orders_in_queue: int = 0
    orders_settled: int = 0
    items_total: int = 0
    items_in_queue: int = 0
    items_delivered: int = 0
    items_refunded: int = 0
    queue: list[SupplierQueueDTO] = field(default_factory=list)
    rate_limits: list[RateLimitStateDTO] = field(default_factory=list)
    dead_commands: int = 0
