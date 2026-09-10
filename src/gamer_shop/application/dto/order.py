from dataclasses import dataclass, field
from datetime import datetime

from gamer_shop.application.enums import OrderItemStatus, OrderStatus, SupplierName


@dataclass(frozen=True, slots=True)
class CreateOrderLineDTO:
    """Строка запроса. Количество разворачивается в отдельные позиции:
    один код на позицию — это проще проверять, чем счётчик количества."""

    sku: str
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class CreateOrderDTO:
    lines: list[CreateOrderLineDTO]


@dataclass(frozen=True, slots=True)
class OrderItemDTO:
    id: str
    order_id: str
    position: int
    sku: str
    price: int
    currency: str
    supplier: SupplierName
    status: OrderItemStatus
    created_at: datetime
    updated_at: datetime
    delivery_attempts: int = 0
    delivered_at: datetime | None = None
    refunded_at: datetime | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class OrderDTO:
    id: str
    total_amount: int
    currency: str
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    settled_at: datetime | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class OrderItemViewDTO:
    item: OrderItemDTO
    code: str | None = None
    # Кто выдал на самом деле. От назначенного отличается после фолбэка.
    delivered_by: str | None = None


@dataclass(frozen=True, slots=True)
class OrderViewDTO:
    """Заказ вместе с позициями и выданными кодами."""

    order: OrderDTO
    items: list[OrderItemViewDTO] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ItemStatusCountsDTO:
    """Срез позиций заказа — на нём строится расчёт заказа."""

    total: int
    delivered: int
    refunded: int
    delivered_amount: int
    refunded_amount: int

    @property
    def settled(self) -> int:
        return self.delivered + self.refunded

    @property
    def all_settled(self) -> bool:
        return self.total > 0 and self.settled == self.total
