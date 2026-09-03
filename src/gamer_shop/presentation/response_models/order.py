from datetime import datetime

from .base import ResponseBase


class OrderOut(ResponseBase):
    id: str
    sku: str
    price: int
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    delivered_at: datetime | None = None
    failure_reason: str | None = None
    #: Заполняется только после успешной выдачи.
    code: str | None = None
    supplier: str | None = None
