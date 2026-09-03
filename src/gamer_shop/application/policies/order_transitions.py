"""Переходы статусов заказа."""

from gamer_shop.application.enums import OrderStatus

ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.PAID, OrderStatus.PAYMENT_FAILED}),
    OrderStatus.PAID: frozenset({OrderStatus.DELIVERING}),
    OrderStatus.DELIVERING: frozenset(
        {OrderStatus.DELIVERED, OrderStatus.OUT_OF_STOCK, OrderStatus.DELIVERY_FAILED}
    ),
    # Из восстановимых статусов выдача перезапускается.
    OrderStatus.OUT_OF_STOCK: frozenset({OrderStatus.DELIVERING}),
    OrderStatus.DELIVERY_FAILED: frozenset({OrderStatus.DELIVERING}),
    # Финальные.
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.PAYMENT_FAILED: frozenset(),
}


def can_transition(source: OrderStatus, target: OrderStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[source]


def sources_for(target: OrderStatus) -> frozenset[OrderStatus]:
    """Из каких статусов разрешён переход в target — для условного UPDATE."""
    return frozenset(src for src, targets in ALLOWED_TRANSITIONS.items() if target in targets)
