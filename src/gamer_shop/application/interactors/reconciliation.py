"""Сверка и восстановление."""

from gamer_shop.application.dto import (
    DeliveryResultDTO,
    ReconciliationReportDTO,
    WebhookOutcome,
)
from gamer_shop.application.exceptions import ApplicationException
from gamer_shop.application.enums import DELIVERABLE_ITEM_STATUSES, OrderItemStatus
from gamer_shop.application.interfaces import DeliveryLogger, PaymentsLogger, UoW
from gamer_shop.application.interactors.delivery import DeliverOrderItemInteractor
from gamer_shop.application.interactors.settlement import SettleOrderInteractor
from gamer_shop.application.interactors.payment_webhook import HandlePaymentWebhookInteractor
from gamer_shop.application.repositories import (
    DiscrepancyRepository,
    LedgerRepository,
    OrderItemRepository,
    OrderRepository,
    OutboxRepository,
    PaymentEventRepository,
    ReconciliationRepository,
    SupplierRequestRepository,
)

REPORT_LIMIT = 100


class ReconciliationInteractor:
    """Расхождения между деньгами, заказами и выдачей."""

    def __init__(
        self,
        reconciliation: ReconciliationRepository,
        events: PaymentEventRepository,
        requests: SupplierRequestRepository,
        ledger: LedgerRepository,
        outbox: OutboxRepository,
        discrepancies: DiscrepancyRepository,
        stale_after_seconds: int,
    ) -> None:
        self._reconciliation = reconciliation
        self._events = events
        self._requests = requests
        self._ledger = ledger
        self._outbox = outbox
        self._discrepancies = discrepancies
        self._stale_after_seconds = stale_after_seconds

    async def execute(self, older_than_seconds: int | None = None) -> ReconciliationReportDTO:
        age = self._stale_after_seconds if older_than_seconds is None else older_than_seconds
        balances = await self._ledger.balances()
        # Сумма дебетов минус сумма кредитов обязана быть нулём.
        total = sum(b.balance for b in balances)

        return ReconciliationReportDTO(
            paid_not_delivered=await self._reconciliation.paid_not_delivered(age, REPORT_LIMIT),
            delivered_not_paid=await self._reconciliation.delivered_not_paid(REPORT_LIMIT),
            stuck_delivering=await self._reconciliation.stuck_delivering(age, REPORT_LIMIT),
            unresolved_supplier_requests=await self._requests.unresolved(REPORT_LIMIT),
            unapplied_events=await self._events.unapplied_report(REPORT_LIMIT),
            ledger_balances=balances,
            ledger_is_balanced=total == 0,
            open_discrepancies=await self._discrepancies.open_ones(REPORT_LIMIT),
            discrepancy_counts=await self._discrepancies.counts_by_kind(),
            money_mismatch=await self._reconciliation.money_mismatch(REPORT_LIMIT),
            stock_drift=await self._reconciliation.stock_drift(),
            outbox_depth=await self._outbox.stats(),
            dead_commands=await self._outbox.dead(REPORT_LIMIT),
        )


class RetryStuckOrdersInteractor:
    """Сетка безопасности поверх аутбокса.

    Обычный путь — команда в очереди; сюда попадает только то, что из очереди
    выпало: команда умерла, процесс не дошёл до постановки, заказ остался
    нерассчитанным. Каждая находка здесь — повод посмотреть, почему аутбокс
    её потерял, поэтому она и логируется отдельно.
    """

    def __init__(
        self,
        orders: OrderRepository,
        items: OrderItemRepository,
        deliver: DeliverOrderItemInteractor,
        settle: SettleOrderInteractor,
        logger: DeliveryLogger,
        stale_after_seconds: int,
        batch_size: int,
    ) -> None:
        self._orders = orders
        self._items = items
        self._deliver = deliver
        self._settle = settle
        self._logger = logger
        self._stale_after_seconds = stale_after_seconds
        self._batch_size = batch_size

    async def execute(self) -> list[DeliveryResultDTO]:
        statuses = frozenset(DELIVERABLE_ITEM_STATUSES | {OrderItemStatus.DELIVERING})
        stale = await self._items.find_stale(
            statuses, self._stale_after_seconds, self._batch_size
        )
        results: list[DeliveryResultDTO] = []
        if stale:
            self._logger.warning('retry_stuck_items_batch', count=len(stale))
            for item_id in stale:
                try:
                    results.append(await self._deliver.execute(item_id))
                except ApplicationException as exc:
                    # Выдача не завершилась — это штатный исход, повтор придёт
                    # следующим проходом.
                    self._logger.info(
                        'retry_stuck_item_pending', order_item_id=item_id, reason=str(exc)
                    )

        # Заказы, у которых позиции уже терминальны, а расчёта не было.
        unsettled = await self._orders.find_unsettled(
            self._stale_after_seconds, self._batch_size
        )
        if unsettled:
            self._logger.warning('settle_stuck_orders_batch', count=len(unsettled))
            for order_id in unsettled:
                await self._settle.execute(order_id)
        return results


class ApplyOrphanEventsInteractor:
    """Вебхуки, принятые раньше заказа.

    Событие тогда сохраняется без применения; здесь применяется,
    как только заказ появился.
    """

    def __init__(
        self,
        uow: UoW,
        events: PaymentEventRepository,
        handler: HandlePaymentWebhookInteractor,
        logger: PaymentsLogger,
        batch_size: int,
    ) -> None:
        self._uow = uow
        self._events = events
        self._handler = handler
        self._logger = logger
        self._batch_size = batch_size

    async def execute(self) -> list[str]:
        pending = await self._events.list_unapplied(self._batch_size)
        applied: list[str] = []
        for event in pending:
            # Через replay, а не execute: первый рубеж отсёк бы событие как дубль.
            result = await self._handler.replay(event)
            if result.outcome is WebhookOutcome.APPLIED:
                applied.append(event.event_id)
                self._logger.info('orphan_event_applied', event_id=event.event_id)
        return applied
