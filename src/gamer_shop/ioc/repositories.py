from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.repositories import (
    DeliveryRepository,
    LedgerRepository,
    OrderRepository,
    PaymentEventRepository,
    ProductRepository,
    ReconciliationRepository,
    StockRepository,
    SupplierRequestRepository,
)
from gamer_shop.infrastructure.repositories import (
    SqlAlchemyDeliveryRepository,
    SqlAlchemyLedgerRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyPaymentEventRepository,
    SqlAlchemyProductRepository,
    SqlAlchemyReconciliationRepository,
    SqlAlchemyStockRepository,
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
    def payment_events(self, session: AsyncSession) -> PaymentEventRepository:
        return SqlAlchemyPaymentEventRepository(session)

    @provide
    def deliveries(self, session: AsyncSession) -> DeliveryRepository:
        return SqlAlchemyDeliveryRepository(session)

    @provide
    def supplier_requests(self, session: AsyncSession) -> SupplierRequestRepository:
        return SqlAlchemySupplierRequestRepository(session)

    @provide
    def ledger(self, session: AsyncSession) -> LedgerRepository:
        return SqlAlchemyLedgerRepository(session)

    @provide
    def reconciliation(self, session: AsyncSession) -> ReconciliationRepository:
        return SqlAlchemyReconciliationRepository(session)
