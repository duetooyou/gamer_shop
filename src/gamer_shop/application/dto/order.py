from dataclasses import dataclass
from datetime import datetime

from gamer_shop.application.enums import OrderStatus


@dataclass(frozen=True, slots=True)
class CreateOrderDTO:
    sku: str


@dataclass(frozen=True, slots=True)
class OrderDTO:
    id: str
    sku: str
    price: int
    currency: str
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    delivered_at: datetime | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class OrderViewDTO:
    """Заказ вместе с выданным кодом."""

    order: OrderDTO
    code: str | None
    supplier: str | None
