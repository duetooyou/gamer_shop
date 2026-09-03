from dataclasses import dataclass, field

from gamer_shop.application.dto.ledger import AccountBalanceDTO


@dataclass(frozen=True, slots=True)
class OrderProblemDTO:
    order_id: str
    status: str
    sku: str
    amount: int
    age_seconds: int
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationReportDTO:
    """Отчёт сверки."""

    paid_not_delivered: list[OrderProblemDTO] = field(default_factory=list)
    # Аномалия: в норме список пуст.
    delivered_not_paid: list[OrderProblemDTO] = field(default_factory=list)
    stuck_delivering: list[OrderProblemDTO] = field(default_factory=list)
    # Обращения с неизвестным исходом после таймаута.
    unresolved_supplier_requests: list[OrderProblemDTO] = field(default_factory=list)
    # Вебхуки раньше заказа либо с расхождением суммы.
    unapplied_events: list[OrderProblemDTO] = field(default_factory=list)
    # Сумма всех balance обязана быть нулём.
    ledger_balances: list[AccountBalanceDTO] = field(default_factory=list)
    ledger_is_balanced: bool = True
    stock_drift: list[str] = field(default_factory=list)
