from enum import StrEnum


class OrderStatus(StrEnum):
    """Статус заказа — проекция статусов его позиций.

    После оплаты заказ сам по себе ничего не решает: он закрывается тогда,
    когда каждая позиция дошла до терминального состояния.
    """

    CREATED = 'created'
    PAID = 'paid'
    DELIVERING = 'delivering'
    DELIVERED = 'delivered'                      # выданы все позиции
    PARTIALLY_DELIVERED = 'partially_delivered'  # часть выдана, за остальное возврат
    REFUNDED = 'refunded'                        # не выдано ничего, деньги вернули
    PAYMENT_FAILED = 'payment_failed'


class OrderItemStatus(StrEnum):
    """Статус позиции. Позиция — одна единица товара и ровно один код."""

    PENDING = 'pending'                  # ждёт оплаты
    PAID = 'paid'                        # оплачена, ждёт выдачи
    DELIVERING = 'delivering'
    DELIVERED = 'delivered'              # терминальная
    OUT_OF_STOCK = 'out_of_stock'        # восстановимая
    DELIVERY_FAILED = 'delivery_failed'  # восстановимая
    REFUNDED = 'refunded'                # терминальная


# Из финальных статусов выхода нет: повтор оплаты или выдачи их не меняет.
FINAL_STATUSES = frozenset(
    {
        OrderStatus.DELIVERED,
        OrderStatus.PARTIALLY_DELIVERED,
        OrderStatus.REFUNDED,
        OrderStatus.PAYMENT_FAILED,
    }
)

# Позиция дошла до конца: выдана либо возвращена. Промежуточных исходов
# у денег нет — на этом держится «оплачено = выдано + возвращено».
TERMINAL_ITEM_STATUSES = frozenset(
    {OrderItemStatus.DELIVERED, OrderItemStatus.REFUNDED}
)

RECOVERABLE_ITEM_STATUSES = frozenset(
    {OrderItemStatus.OUT_OF_STOCK, OrderItemStatus.DELIVERY_FAILED}
)

# Статусы, из которых выдачу позиции можно (пере)запустить.
DELIVERABLE_ITEM_STATUSES = frozenset(
    {
        OrderItemStatus.PAID,
        OrderItemStatus.OUT_OF_STOCK,
        OrderItemStatus.DELIVERY_FAILED,
    }
)


class PaymentStatus(StrEnum):
    PAID = 'paid'
    FAILED = 'failed'


class ProductType(StrEnum):
    TOPUP = 'topup'
    KEY = 'key'
    SUBSCRIPTION = 'subscription'
    GIFTCARD = 'giftcard'


class SupplierName(StrEnum):
    """Поставщик закреплён за товаром; второй служит резервом."""

    A = 'a'
    B = 'b'


def fallback_for(supplier: SupplierName) -> SupplierName:
    return SupplierName.B if supplier is SupplierName.A else SupplierName.A


class SupplierRequestState(StrEnum):
    """Состояние обращения к поставщику.

    REFUSED — код точно не выдан, можно на фолбэк. UNKNOWN — таймаут,
    код мог быть выдан, фолбэк запрещён.
    """

    PENDING = 'pending'
    OK = 'ok'
    REFUSED = 'refused'
    UNKNOWN = 'unknown'


class LedgerAccount(StrEnum):
    CASH_IN = 'cash_in'                        # деньги от платёжной системы
    CUSTOMER_LIABILITY = 'customer_liability'  # обязательство выдать товар
    REVENUE = 'revenue'                        # признаётся после выдачи
    REFUND_PAYABLE = 'refund_payable'          # обязательство вернуть деньги
    CASH_OUT = 'cash_out'                      # деньги, ушедшие обратно


class LedgerDirection(StrEnum):
    DEBIT = 'debit'
    CREDIT = 'credit'


class DiscrepancyKind(StrEnum):
    """Как именно поставщик разошёлся с реальностью."""

    # Прислал код, уже закреплённый за другой позицией.
    DUPLICATE_CODE = 'duplicate_code'
    # Ответ подписан не тем request_id, который отправляли.
    FOREIGN_RESPONSE = 'foreign_response'
    # Ответил ошибкой, а код на самом деле выдал.
    PHANTOM_ISSUE = 'phantom_issue'
    # Код выдан, но использовать его нельзя.
    UNUSABLE_CODE = 'unusable_code'


class DiscrepancyState(StrEnum):
    OPEN = 'open'
    RESOLVED = 'resolved'


class OutboxCommandKind(StrEnum):
    """Что именно надо сделать во внешнем мире."""

    DELIVER_ITEM = 'deliver_item'
    REFUND_ITEM = 'refund_item'
    SETTLE_ORDER = 'settle_order'


class OutboxState(StrEnum):
    """DEAD — попытки исчерпаны; такие команды показывает сверка."""

    PENDING = 'pending'
    DONE = 'done'
    DEAD = 'dead'


# Партиция по умолчанию, пока команда не привязана к поставщику.
DEFAULT_PARTITION = 'default'

# Приоритет: оплаченное обслуживается раньше неоплаченного.
PRIORITY_PAID = 10
PRIORITY_NORMAL = 0
