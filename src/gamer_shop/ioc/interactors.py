from dishka import Provider, Scope, provide

from gamer_shop.application.interactors import (
    ApplyOrphanEventsInteractor,
    CreateOrderInteractor,
    DeliverOrderItemInteractor,
    GetOrderInteractor,
    HandlePaymentWebhookInteractor,
    OrderStateAtInteractor,
    OutboxRelayInteractor,
    PeriodTotalsInteractor,
    ProgressInteractor,
    PruneOutboxInteractor,
    ReconciliationInteractor,
    RefillStockInteractor,
    RefundItemInteractor,
    RetryStuckOrdersInteractor,
    SettleOrderInteractor,
    StorefrontInteractor,
)
from gamer_shop.application.interfaces import (
    Clock,
    CommandDispatcher,
    DeliveryLogger,
    PaymentGateway,
    PaymentsLogger,
    SupplierClient,
    UoW,
)
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
        self, orders: OrderRepository, items: OrderItemRepository,
        deliveries: DeliveryRepository,
    ) -> GetOrderInteractor:
        return GetOrderInteractor(orders, items, deliveries)

    @provide
    def storefront(self, products: ProductRepository) -> StorefrontInteractor:
        return StorefrontInteractor(products)

    @provide
    def refill_stock(self, uow: UoW, stock: StockRepository) -> RefillStockInteractor:
        return RefillStockInteractor(uow, stock)

    @provide
    def handle_webhook(
        self, uow: UoW, orders: OrderRepository, items: OrderItemRepository,
        events: PaymentEventRepository, ledger: LedgerRepository,
        outbox: OutboxRepository, logger: PaymentsLogger,
    ) -> HandlePaymentWebhookInteractor:
        return HandlePaymentWebhookInteractor(
            uow, orders, items, events, ledger, outbox, logger
        )

    @provide
    def deliver_item(
        self, config: Config, uow: UoW, orders: OrderRepository,
        items: OrderItemRepository, deliveries: DeliveryRepository,
        requests: SupplierRequestRepository, stock: StockRepository,
        ledger: LedgerRepository, outbox: OutboxRepository,
        discrepancies: DiscrepancyRepository, limits: SupplierRateLimitRepository,
        client: SupplierClient, logger: DeliveryLogger,
    ) -> DeliverOrderItemInteractor:
        return DeliverOrderItemInteractor(
            uow, orders, items, deliveries, requests, stock, ledger, outbox,
            discrepancies, limits, client, logger,
            stale_after_seconds=config.delivery.stuck_after_seconds,
            max_attempts_before_refund=config.delivery.max_attempts_before_refund,
            permit_wait_seconds=config.supplier.permit_wait_seconds,
        )

    @provide
    def refund_item(
        self, uow: UoW, items: OrderItemRepository, ledger: LedgerRepository,
        outbox: OutboxRepository, gateway: PaymentGateway, logger: PaymentsLogger,
    ) -> RefundItemInteractor:
        return RefundItemInteractor(uow, items, ledger, outbox, gateway, logger)

    @provide
    def settle_order(
        self, uow: UoW, orders: OrderRepository, items: OrderItemRepository,
        ledger: LedgerRepository, logger: PaymentsLogger,
    ) -> SettleOrderInteractor:
        return SettleOrderInteractor(uow, orders, items, ledger, logger)

    @provide
    def outbox_relay(
        self, config: Config, uow: UoW, outbox: OutboxRepository,
        limits: SupplierRateLimitRepository, dispatcher: CommandDispatcher,
        logger: DeliveryLogger,
    ) -> OutboxRelayInteractor:
        return OutboxRelayInteractor(
            uow, outbox, limits, dispatcher, logger,
            batch_size=config.outbox.batch_size,
            lease_seconds=config.outbox.lease_seconds,
            max_attempts=config.outbox.max_attempts,
            backoff_base=config.outbox.backoff_base,
            backoff_max=config.outbox.backoff_max,
            poll_interval_seconds=config.outbox.poll_interval_seconds,
        )

    @provide
    def prune_outbox(
        self, config: Config, uow: UoW, outbox: OutboxRepository, logger: DeliveryLogger
    ) -> PruneOutboxInteractor:
        return PruneOutboxInteractor(
            uow, outbox, logger, keep_seconds=config.outbox.keep_done_seconds
        )

    @provide
    def reconciliation(
        self, config: Config, reconciliation: ReconciliationRepository,
        events: PaymentEventRepository, requests: SupplierRequestRepository,
        ledger: LedgerRepository, outbox: OutboxRepository,
        discrepancies: DiscrepancyRepository,
    ) -> ReconciliationInteractor:
        return ReconciliationInteractor(
            reconciliation, events, requests, ledger, outbox, discrepancies,
            stale_after_seconds=config.delivery.stuck_after_seconds,
        )

    @provide
    def retry_stuck(
        self, config: Config, orders: OrderRepository, items: OrderItemRepository,
        deliver: DeliverOrderItemInteractor, settle: SettleOrderInteractor,
        logger: DeliveryLogger,
    ) -> RetryStuckOrdersInteractor:
        return RetryStuckOrdersInteractor(
            orders, items, deliver, settle, logger,
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

    @provide
    def order_state_at(
        self, history: OrderHistoryRepository, ledger: LedgerHistoryRepository,
        clock: Clock,
    ) -> OrderStateAtInteractor:
        return OrderStateAtInteractor(history, ledger, clock)

    @provide
    def period_totals(
        self, history: OrderHistoryRepository, ledger: LedgerHistoryRepository,
        clock: Clock,
    ) -> PeriodTotalsInteractor:
        return PeriodTotalsInteractor(history, ledger, clock)

    @provide
    def progress(self, progress: ProgressRepository) -> ProgressInteractor:
        return ProgressInteractor(progress)
