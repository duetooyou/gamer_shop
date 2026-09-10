from datetime import datetime

from .base import ResponseBase


class OrderItemOut(ResponseBase):
    id: str
    position: int
    sku: str
    price: int
    currency: str
    #: Назначенный поставщик товара.
    supplier: str
    status: str
    #: Заполняется только после успешной выдачи.
    code: str | None = None
    #: Кто выдал фактически: после фолбэка отличается от supplier.
    delivered_by: str | None = None
    delivered_at: datetime | None = None
    refunded_at: datetime | None = None
    failure_reason: str | None = None


class OrderOut(ResponseBase):
    id: str
    total_amount: int
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    settled_at: datetime | None = None
    failure_reason: str | None = None
    items: list[OrderItemOut] = []
