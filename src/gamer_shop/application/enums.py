from enum import StrEnum


class OrderStatus(StrEnum):
    CREATED = 'created'
    PAID = 'paid'
    DELIVERING = 'delivering'
    DELIVERED = 'delivered'
    PAYMENT_FAILED = 'payment_failed'
    OUT_OF_STOCK = 'out_of_stock'        # восстановимый
    DELIVERY_FAILED = 'delivery_failed'  # восстановимый


# Из финальных статусов выхода нет: повтор оплаты или выдачи их не меняет.
FINAL_STATUSES = frozenset({OrderStatus.DELIVERED, OrderStatus.PAYMENT_FAILED})

RECOVERABLE_STATUSES = frozenset({OrderStatus.OUT_OF_STOCK, OrderStatus.DELIVERY_FAILED})

# Статусы, из которых выдачу можно (пере)запустить.
DELIVERABLE_STATUSES = frozenset(
    {OrderStatus.PAID, OrderStatus.OUT_OF_STOCK, OrderStatus.DELIVERY_FAILED}
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
    """A — основной, B — резервный."""

    A = 'a'
    B = 'b'


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


class LedgerDirection(StrEnum):
    DEBIT = 'debit'
    CREDIT = 'credit'
