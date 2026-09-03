"""Сверка и восстановление."""

from gamer_shop.application.dto import (
    DeliveryResultDTO,
    ReconciliationReportDTO,
    WebhookOutcome,
)
from gamer_shop.application.enums import DELIVERABLE_STATUSES, OrderStatus
from gamer_shop.application.interfaces import DeliveryLogger, PaymentsLogger, UoW
from gamer_shop.application.interactors.delivery import DeliverOrderInteractor
from gamer_shop.application.interactors.payment_webhook import HandlePaymentWebhookInteractor
from gamer_shop.application.repositories import (
    LedgerRepository,
    OrderRepository,
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
        stale_after_seconds: int,
    ) -> None:
        self._reconciliation = reconciliation
        self._events = events
        self._requests = requests
        self._ledger = ledger
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
            stock_drift=await self._reconciliation.stock_drift(),
        )


class RetryStuckOrdersInteractor:
    """Дожатие «зависших» заказов.

    Задача не делает ничего своего: переиспользует тот же интерактор выдачи,
    а значит и тот же детерминированный request_id.
    """

    def __init__(
        self,
        orders: OrderRepository,
        deliver: DeliverOrderInteractor,
        logger: DeliveryLogger,
        stale_after_seconds: int,
        batch_size: int,
    ) -> None:
        self._orders = orders
        self._deliver = deliver
        self._logger = logger
        self._stale_after_seconds = stale_after_seconds
        self._batch_size = batch_size

    async def execute(self) -> list[DeliveryResultDTO]:
        statuses = frozenset(DELIVERABLE_STATUSES | {OrderStatus.DELIVERING})
        stale = await self._orders.find_stale(
            statuses, self._stale_after_seconds, self._batch_size
        )
        if not stale:
            return []

        self._logger.info('retry_stuck_orders_batch', count=len(stale))
        results: list[DeliveryResultDTO] = []
        for order_id in stale:
            results.append(await self._deliver.execute(order_id))
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
