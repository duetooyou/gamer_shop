from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.interfaces import UoW
from gamer_shop.application.repositories import (
    DeliveryRepository,
    DiscrepancyRepository,
    LedgerHistoryRepository,
    LedgerRepository,
    OrderHistoryRepository,
    OrderItemRepository,
    OrderRepository,
    OutboxRepository,
    PaymentEventRepository,
    ProductRepository,
    ProgressRepository,
    ReconciliationRepository,
    StockRepository,
    SupplierRateLimitRepository,
    SupplierRequestRepository,
)
from gamer_shop.infrastructure.config import Config
from gamer_shop.infrastructure.repositories import (
    SqlAlchemyDeliveryRepository,
    SqlAlchemyDiscrepancyRepository,
    SqlAlchemyLedgerHistoryRepository,
    SqlAlchemyLedgerRepository,
    SqlAlchemyOrderHistoryRepository,
    SqlAlchemyOrderItemRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyOutboxRepository,
    SqlAlchemyPaymentEventRepository,
    SqlAlchemyProductRepository,
    SqlAlchemyProgressRepository,
    SqlAlchemyReconciliationRepository,
    SqlAlchemyStockRepository,
    SqlAlchemySupplierRateLimitRepository,
    SqlAlchemySupplierRequestRepository,
)


class RepositoriesProvider(Provider):
    """Репозитории делят одну сессию: транзакция вебхука пишет событие,
    заказ и проводки журнала — они должны коммититься вместе."""

    scope = Scope.REQUEST

    @provide
    def products(self, session: AsyncSession) -> ProductRepository:
        return SqlAlchemyProductRepository(session)

    @provide
    def stock(self, session: AsyncSession) -> StockRepository:
        return SqlAlchemyStockRepository(session)

    @provide
    def orders(self, session: AsyncSession) -> OrderRepository:
        return SqlAlchemyOrderRepository(session)

    @provide
    def outbox(self, session: AsyncSession, uow: UoW) -> OutboxRepository:
        # UoW нужен репозиторию, чтобы отказать в постановке команды вне
        # транзакции: иначе она разъедется с изменением состояния.
        return SqlAlchemyOutboxRepository(session, uow)

    @provide
    def order_items(self, session: AsyncSession) -> OrderItemRepository:
        return SqlAlchemyOrderItemRepository(session)

    @provide
    def payment_events(self, session: AsyncSession) -> PaymentEventRepository:
        return SqlAlchemyPaymentEventRepository(session)

    @provide
    def deliveries(self, session: AsyncSession) -> DeliveryRepository:
        return SqlAlchemyDeliveryRepository(session)

    @provide
    def supplier_requests(self, session: AsyncSession) -> SupplierRequestRepository:
        return SqlAlchemySupplierRequestRepository(session)

    @provide
    def discrepancies(self, session: AsyncSession) -> DiscrepancyRepository:
        return SqlAlchemyDiscrepancyRepository(session)

    @provide
    def ledger(self, session: AsyncSession) -> LedgerRepository:
        return SqlAlchemyLedgerRepository(session)

    @provide
    def reconciliation(self, session: AsyncSession) -> ReconciliationRepository:
        return SqlAlchemyReconciliationRepository(session)

    @provide
    def order_history(self, session: AsyncSession) -> OrderHistoryRepository:
        return SqlAlchemyOrderHistoryRepository(session)

    @provide
    def ledger_history(self, session: AsyncSession) -> LedgerHistoryRepository:
        return SqlAlchemyLedgerHistoryRepository(session)

    @provide
    def rate_limits(
        self, session: AsyncSession, config: Config
    ) -> SupplierRateLimitRepository:
        # Настройка — источник правды для ёмкости и лимита; строка в базе
        # заводится и подстраивается под неё на первом же обращении.
        return SqlAlchemySupplierRateLimitRepository(session, config.supplier)

    @provide
    def progress(
        self, session: AsyncSession, limits: SupplierRateLimitRepository
    ) -> ProgressRepository:
        return SqlAlchemyProgressRepository(session, limits)
