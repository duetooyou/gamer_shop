from datetime import datetime
from typing import Any

from .base import ResponseBase


class OrderEventOut(ResponseBase):
    id: int
    order_item_id: str | None = None
    kind: str
    from_status: str | None = None
    to_status: str | None = None
    payload: dict[str, Any]
    occurred_at: datetime


class MoneySnapshotOut(ResponseBase):
    paid: int
    delivered: int
    refunded: int
    # Оплачено, но на тот момент ещё не разрешилось ни выдачей, ни возвратом.
    outstanding: int


class ItemSnapshotOut(ResponseBase):
    order_item_id: str
    position: int
    sku: str
    price: int
    currency: str
    supplier: str
    status: str
    code_issued: bool


class OrderStateAtOut(ResponseBase):
    order_id: str
    as_of: datetime
    # false — заказа на тот момент ещё не существовало.
    exists: bool
    status: str | None = None
    total_amount: int = 0
    currency: str | None = None
    items: list[ItemSnapshotOut] = []
    money: MoneySnapshotOut | None = None
    events: list[OrderEventOut] = []


class PeriodTotalsOut(ResponseBase):
    period_from: datetime
    period_to: datetime
    paid: int
    delivered: int
    refunded: int
    opening_outstanding: int
    closing_outstanding: int
    outstanding_change: int
    # Пришло за период столько же, сколько выдано, возвращено и осталось
    # висеть обязательством. false — расходятся данные, а не отчёт.
    balanced: bool
    orders_created: int
    orders_settled: int
    items_delivered: int
    items_refunded: int
