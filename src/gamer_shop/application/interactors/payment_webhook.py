"""Обработка вебхука оплаты."""

from gamer_shop.application.dto import (
    NewCommandDTO,
    PaymentWebhookDTO,
    WebhookOutcome,
    WebhookResultDTO,
)
from gamer_shop.application.enums import (
    PRIORITY_PAID,
    LedgerAccount,
    OrderStatus,
    OutboxCommandKind,
    PaymentStatus,
)
from gamer_shop.application.interfaces import PaymentsLogger, UoW
from gamer_shop.application.policies import sources_for
from gamer_shop.application.repositories import (
    LedgerRepository,
    OrderItemRepository,
    OrderRepository,
    OutboxRepository,
    PaymentEventRepository,
)


class HandlePaymentWebhookInteractor:
    """Идемпотентная обработка вебхука.

    Три рубежа: ON CONFLICT по event_id, FOR UPDATE по заказу, условный UPDATE
    по статусу. Наружу всегда 200 — 5xx заставит платёжку повторять доставку.

    Выдача не ставится в очередь после коммита, а пишется командой в аутбокс
    внутри той же транзакции: иначе смерть процесса между коммитом и
    постановкой задачи теряла бы выдачу по оплаченному заказу.
    """

    def __init__(
        self,
        uow: UoW,
        orders: OrderRepository,
        items: OrderItemRepository,
        events: PaymentEventRepository,
        ledger: LedgerRepository,
        outbox: OutboxRepository,
        logger: PaymentsLogger,
    ) -> None:
        self._uow = uow
        self._orders = orders
        self._items = items
        self._events = events
        self._ledger = ledger
        self._outbox = outbox
        self._logger = logger

    async def execute(self, dto: PaymentWebhookDTO) -> WebhookResultDTO:
        log = self._logger.bind(
            event_id=dto.event_id,
            order_id=dto.order_id,
            payment_status=dto.status.value,
            amount=dto.amount,
            currency=dto.currency,
        )

        async with self._uow:
            # Рубеж 1: дубль не проходит дальше вставки.
            if not await self._events.insert_if_new(dto):
                log.info('webhook_duplicate', outcome=WebhookOutcome.DUPLICATE.value)
                return WebhookResultDTO(WebhookOutcome.DUPLICATE, dto.order_id)

            return await self._apply(dto, log)

    async def replay(self, dto: PaymentWebhookDTO) -> WebhookResultDTO:
        """Применить событие, принятое раньше заказа.

        Шаг идемпотентности пропущен намеренно: событие уже в журнале,
        и первый рубеж отверг бы его как дубль.
        """
        log = self._logger.bind(event_id=dto.event_id, order_id=dto.order_id, replay=True)
        async with self._uow:
            return await self._apply(dto, log)

    async def _apply(self, dto: PaymentWebhookDTO, log) -> WebhookResultDTO:
        # Рубеж 2: сериализуем параллельные вебхуки по одному заказу.
        order = await self._orders.lock_by_id(dto.order_id)
        if order is None:
            # Вебхук пришёл раньше заказа. Событие сохранено, фоновая задача
            # применит его позже. Наружу 200 — платёжке повторять нечего.
            await self._events.mark_rejected(dto.event_id, 'order_not_found')
            log.warning('webhook_order_not_found', outcome=WebhookOutcome.ORDER_NOT_FOUND.value)
            return WebhookResultDTO(WebhookOutcome.ORDER_NOT_FOUND, dto.order_id)

        if dto.amount != order.total_amount or dto.currency != order.currency:
            await self._events.mark_rejected(dto.event_id, 'amount_mismatch')
            log.error(
                'webhook_amount_mismatch',
                outcome=WebhookOutcome.AMOUNT_MISMATCH.value,
                expected_amount=order.total_amount,
                expected_currency=order.currency,
            )
            return WebhookResultDTO(WebhookOutcome.AMOUNT_MISMATCH, dto.order_id)

        if dto.status is PaymentStatus.PAID:
            return await self._apply_paid(dto, order.total_amount, order.currency, log)
        return await self._apply_failed(dto, log)

    async def _apply_paid(self, dto, amount: int, currency: str, log) -> WebhookResultDTO:
        # Рубеж 3: переход делает ровно один вызов, остальные получают rowcount=0.
        changed = await self._orders.try_transition(
            dto.order_id, OrderStatus.PAID, sources_for(OrderStatus.PAID)
        )
        await self._events.mark_applied(dto.event_id)

        if not changed:
            # Заказ уже оплачен, выдан или отменён — ничего не меняем.
            log.info('webhook_no_change', outcome=WebhookOutcome.ALREADY_FINAL.value)
            return WebhookResultDTO(WebhookOutcome.ALREADY_FINAL, dto.order_id)

        await self._ledger.record_double_entry(
            order_id=dto.order_id,
            debit_account=LedgerAccount.CASH_IN,
            credit_account=LedgerAccount.CUSTOMER_LIABILITY,
            amount=amount,
            currency=currency,
            ref_type='payment',
            ref_id=dto.event_id,
            idempotency_key=f'{dto.order_id}:paid',
        )
        # Позиции переходят в paid и получают по команде выдачи каждая:
        # они идут к разным поставщикам и завершаются независимо.
        items = await self._items.mark_paid(dto.order_id)
        for item in items:
            await self._outbox.enqueue(
                NewCommandDTO(
                    kind=OutboxCommandKind.DELIVER_ITEM,
                    dedup_key=item.id,
                    payload={'order_item_id': item.id},
                    partition_key=item.supplier.value,
                    priority=PRIORITY_PAID,
                )
            )
        log.info(
            'webhook_applied',
            outcome=WebhookOutcome.APPLIED.value,
            new_status='paid',
            positions=len(items),
        )
        return WebhookResultDTO(WebhookOutcome.APPLIED, dto.order_id, notify_outbox=True)

    async def _apply_failed(self, dto, log) -> WebhookResultDTO:
        changed = await self._orders.try_transition(
            dto.order_id,
            OrderStatus.PAYMENT_FAILED,
            sources_for(OrderStatus.PAYMENT_FAILED),
            failure_reason='payment_failed',
        )
        await self._events.mark_applied(dto.event_id)
        if not changed:
            # Опоздавший failed не откатывает уже выданный заказ.
            log.warning('webhook_late_failure_ignored', outcome=WebhookOutcome.ALREADY_FINAL.value)
            return WebhookResultDTO(WebhookOutcome.ALREADY_FINAL, dto.order_id)
        log.info('webhook_applied', outcome=WebhookOutcome.APPLIED.value, new_status='payment_failed')
        return WebhookResultDTO(WebhookOutcome.APPLIED, dto.order_id)
