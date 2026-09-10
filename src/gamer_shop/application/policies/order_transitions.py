"""Переходы статусов.

Заказ и позиция живут по разным правилам: позиция ходит по своей цепочке до
терминального состояния, заказ закрывается расчётом по всем позициям сразу.
"""

from gamer_shop.application.enums import OrderItemStatus, OrderStatus

ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.PAID, OrderStatus.PAYMENT_FAILED}),
    OrderStatus.PAID: frozenset(
        {
            OrderStatus.DELIVERING,
            # Все позиции могли закрыться раньше, чем заказ показался
            # в delivering: расчёт обязан пройти и из paid.
            OrderStatus.DELIVERED,
            OrderStatus.PARTIALLY_DELIVERED,
            OrderStatus.REFUNDED,
        }
    ),
    # Итог заказа определяет расчёт: всё выдано, часть выдана либо ничего.
    OrderStatus.DELIVERING: frozenset(
        {
            OrderStatus.DELIVERED,
            OrderStatus.PARTIALLY_DELIVERED,
            OrderStatus.REFUNDED,
        }
    ),
    # Финальные.
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.PARTIALLY_DELIVERED: frozenset(),
    OrderStatus.REFUNDED: frozenset(),
    OrderStatus.PAYMENT_FAILED: frozenset(),
}

ALLOWED_ITEM_TRANSITIONS: dict[OrderItemStatus, frozenset[OrderItemStatus]] = {
    OrderItemStatus.PENDING: frozenset({OrderItemStatus.PAID}),
    OrderItemStatus.PAID: frozenset({OrderItemStatus.DELIVERING}),
    OrderItemStatus.DELIVERING: frozenset(
        {
            OrderItemStatus.DELIVERED,
            OrderItemStatus.OUT_OF_STOCK,
            OrderItemStatus.DELIVERY_FAILED,
        }
    ),
    # Из восстановимых выдача перезапускается, а если не вышло — возврат.
    OrderItemStatus.OUT_OF_STOCK: frozenset(
        {OrderItemStatus.DELIVERING, OrderItemStatus.REFUNDED}
    ),
    OrderItemStatus.DELIVERY_FAILED: frozenset(
        {OrderItemStatus.DELIVERING, OrderItemStatus.REFUNDED}
    ),
    # Терминальные: выданное остаётся у покупателя, возвращённое не выдаётся.
    OrderItemStatus.DELIVERED: frozenset(),
    OrderItemStatus.REFUNDED: frozenset(),
}


def can_transition(source: OrderStatus, target: OrderStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[source]


def sources_for(target: OrderStatus) -> frozenset[OrderStatus]:
    """Из каких статусов разрешён переход в target — для условного UPDATE."""
    return frozenset(src for src, targets in ALLOWED_TRANSITIONS.items() if target in targets)


def item_sources_for(target: OrderItemStatus) -> frozenset[OrderItemStatus]:
    return frozenset(
        src for src, targets in ALLOWED_ITEM_TRANSITIONS.items() if target in targets
    )
