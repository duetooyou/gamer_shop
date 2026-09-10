from dataclasses import dataclass, field

from gamer_shop.application.dto.discrepancy import DiscrepancyDTO
from gamer_shop.application.dto.ledger import AccountBalanceDTO
from gamer_shop.application.dto.outbox import OutboxCommandDTO, OutboxStatsDTO


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
    # Расхождения с поставщиком, которые никто не разобрал. В норме пусто:
    # разбор идёт там же, где обнаружение.
    open_discrepancies: list[DiscrepancyDTO] = field(default_factory=list)
    # Сколько расхождений каждого вида встретилось всего — включая разобранные.
    discrepancy_counts: dict[str, int] = field(default_factory=dict)
    # Заказы, где оплачено != выдано + возвращено. В норме пусто.
    money_mismatch: list[OrderProblemDTO] = field(default_factory=list)
    # Сумма всех balance обязана быть нулём.
    ledger_balances: list[AccountBalanceDTO] = field(default_factory=list)
    ledger_is_balanced: bool = True
    stock_drift: list[str] = field(default_factory=list)
    # Глубина очереди команд: по ней виден прогресс разбора всплеска.
    outbox_depth: list[OutboxStatsDTO] = field(default_factory=list)
    # Команды, исчерпавшие попытки. В норме список пуст: каждая строка здесь —
    # работа, которую никто не сделает, пока её не разберут.
    dead_commands: list[OutboxCommandDTO] = field(default_factory=list)
