from dishka import Provider, Scope, provide

from gamer_shop.application.interactors import (
    ApplyOrphanEventsInteractor,
    CreateOrderInteractor,
    DeliverOrderInteractor,
    GetOrderInteractor,
    HandlePaymentWebhookInteractor,
    ReconciliationInteractor,
    RefillStockInteractor,
    RetryStuckOrdersInteractor,
    StorefrontInteractor,
)
from gamer_shop.application.interfaces import (
    DeliveryLogger,
    PaymentsLogger,
    SupplierClient,
    UoW,
)
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
from gamer_shop.infrastructure.config import Config


class InteractorsProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def create_order(
        self, uow: UoW, orders: OrderRepository, products: ProductRepository,
        logger: PaymentsLogger,
    ) -> CreateOrderInteractor:
        return CreateOrderInteractor(uow, orders, products, logger)

    @provide
    def get_order(
        self, orders: OrderRepository, deliveries: DeliveryRepository
    ) -> GetOrderInteractor:
        return GetOrderInteractor(orders, deliveries)

    @provide
    def storefront(self, products: ProductRepository) -> StorefrontInteractor:
        return StorefrontInteractor(products)

    @provide
    def refill_stock(self, uow: UoW, stock: StockRepository) -> RefillStockInteractor:
        return RefillStockInteractor(uow, stock)

    @provide
    def handle_webhook(
        self, uow: UoW, orders: OrderRepository, events: PaymentEventRepository,
        ledger: LedgerRepository, logger: PaymentsLogger,
    ) -> HandlePaymentWebhookInteractor:
        return HandlePaymentWebhookInteractor(uow, orders, events, ledger, logger)

    @provide
    def deliver_order(
        self, config: Config, uow: UoW, orders: OrderRepository,
        deliveries: DeliveryRepository, requests: SupplierRequestRepository,
        stock: StockRepository, ledger: LedgerRepository, client: SupplierClient,
        logger: DeliveryLogger,
    ) -> DeliverOrderInteractor:
        return DeliverOrderInteractor(
            uow, orders, deliveries, requests, stock, ledger, client, logger,
            stale_after_seconds=config.delivery.stuck_after_seconds,
        )

    @provide
    def reconciliation(
        self, config: Config, reconciliation: ReconciliationRepository,
        events: PaymentEventRepository, requests: SupplierRequestRepository,
        ledger: LedgerRepository,
    ) -> ReconciliationInteractor:
        return ReconciliationInteractor(
            reconciliation, events, requests, ledger,
            stale_after_seconds=config.delivery.stuck_after_seconds,
        )

    @provide
    def retry_stuck(
        self, config: Config, orders: OrderRepository, deliver: DeliverOrderInteractor,
        logger: DeliveryLogger,
    ) -> RetryStuckOrdersInteractor:
        return RetryStuckOrdersInteractor(
            orders, deliver, logger,
            stale_after_seconds=config.delivery.stuck_after_seconds,
            batch_size=config.delivery.batch_size,
        )

    @provide
    def apply_orphan_events(
        self, config: Config, uow: UoW, events: PaymentEventRepository,
        handler: HandlePaymentWebhookInteractor, logger: PaymentsLogger,
    ) -> ApplyOrphanEventsInteractor:
        return ApplyOrphanEventsInteractor(
            uow, events, handler, logger, batch_size=config.delivery.batch_size
        )
