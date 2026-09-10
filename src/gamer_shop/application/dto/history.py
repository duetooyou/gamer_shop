from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class OrderEventDTO:
    """Строка журнала. Появившись, не меняется."""

    id: int
    order_id: str
    order_item_id: str | None
    kind: str
    from_status: str | None
    to_status: str | None
    payload: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class MoneySnapshotDTO:
    """Деньги по заказу на момент — из проводок, а не из статусов."""

    paid: int
    delivered: int
    refunded: int

    @property
    def outstanding(self) -> int:
        """Оплачено, но ещё не разрешилось ни выдачей, ни возвратом."""
        return self.paid - self.delivered - self.refunded

    @property
    def balanced(self) -> bool:
        return self.outstanding >= 0


@dataclass(frozen=True, slots=True)
class ItemSnapshotDTO:
    order_item_id: str
    position: int
    sku: str
    price: int
    currency: str
    supplier: str
    status: str
    code_issued: bool


@dataclass(frozen=True, slots=True)
class OrderSnapshotDTO:
    """Состояние заказа и денег на прошлый момент."""

    order_id: str
    as_of: datetime
    exists: bool
    status: str | None = None
    total_amount: int = 0
    currency: str | None = None
    items: list[ItemSnapshotDTO] = field(default_factory=list)
    money: MoneySnapshotDTO = field(
        default_factory=lambda: MoneySnapshotDTO(0, 0, 0)
    )
    events: list[OrderEventDTO] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PeriodTotalsDTO:
    """Итоги за период, посчитанные из журналов.

    Сходимость проверяется тождеством двойной записи: пришло за период
    столько же, сколько за него выдано, возвращено и осталось висеть
    обязательством. Если оно нарушено — расходятся не отчёты, а данные.
    """

    period_from: datetime
    period_to: datetime
    paid: int = 0
    delivered: int = 0
    refunded: int = 0
    opening_outstanding: int = 0
    closing_outstanding: int = 0
    orders_created: int = 0
    orders_settled: int = 0
    items_delivered: int = 0
    items_refunded: int = 0

    @property
    def outstanding_change(self) -> int:
        return self.closing_outstanding - self.opening_outstanding

    @property
    def balanced(self) -> bool:
        return self.paid == self.delivered + self.refunded + self.outstanding_change
