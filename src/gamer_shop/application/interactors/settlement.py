"""Возврат за невыданное и расчёт заказа.

Позиция обязана дойти до одного из двух исходов: код у покупателя либо деньги
обратно. Заказ закрывается тогда, когда таких исходов набралось на все
позиции, и итог заказа — их проекция.
"""

from gamer_shop.application.dto import ItemStatusCountsDTO, NewCommandDTO
from gamer_shop.application.enums import (
    PRIORITY_PAID,
    RECOVERABLE_ITEM_STATUSES,
    LedgerAccount,
    OrderItemStatus,
    OrderStatus,
    OutboxCommandKind,
)
from gamer_shop.application.interfaces import (
    PaymentGateway,
    PaymentsLogger,
    RefundOutcomeKind,
    UoW,
)
from gamer_shop.application.policies import sources_for
from gamer_shop.application.repositories import (
    LedgerRepository,
    OrderItemRepository,
    OrderRepository,
    OutboxRepository,
)


class RefundItemInteractor:
    """Возврат денег за невыданную позицию.

    Идемпотентен трижды: по статусу позиции, по ключу проводки и по
    идентификатору возврата на стороне платёжной системы. Повтор не создаёт
    второго возврата ни в одном из трёх мест.
    """

    def __init__(
        self,
        uow: UoW,
        items: OrderItemRepository,
        ledger: LedgerRepository,
        outbox: OutboxRepository,
        gateway: PaymentGateway,
        logger: PaymentsLogger,
    ) -> None:
        self._uow = uow
        self._items = items
        self._ledger = ledger
        self._outbox = outbox
        self._gateway = gateway
        self._logger = logger

    async def execute(self, item_id: str, reason: str = 'delivery_failed') -> bool:
        log = self._logger.bind(order_item_id=item_id, reason=reason)

        item = await self._items.get_by_id(item_id)
        if item is None:
            log.warning('refund_item_not_found')
            return False

        if item.status is OrderItemStatus.REFUNDED:
            log.info('refund_already_done')
            return False

        if item.status is OrderItemStatus.DELIVERED:
            # Выданное остаётся у покупателя: возврат за него — это подарок
            # за наш счёт, а по деньгам потом не сойдётся.
            log.warning('refund_refused_delivered')
            return False

        if item.status not in RECOVERABLE_ITEM_STATUSES:
            # Позиция ещё в работе — возвращать рано.
            log.info('refund_skipped', status=item.status.value)
            return False

        # Обращение в платёжную систему вне транзакции: HTTP может зависнуть,
        # а держать на нём блокировку строки нельзя.
        refund_id = f'rfnd_{item.id}'
        outcome = await self._gateway.refund(
            refund_id=refund_id,
            order_id=item.order_id,
            amount=item.price,
            currency=item.currency,
            reason=reason,
        )
        if outcome.kind is not RefundOutcomeKind.OK:
            log.error('refund_gateway_failed', outcome=outcome.kind.value, detail=outcome.reason)
            raise RuntimeError(f'возврат не прошёл: {outcome.reason}')

        async with self._uow:
            changed = await self._items.try_transition(
                item.id,
                OrderItemStatus.REFUNDED,
                RECOVERABLE_ITEM_STATUSES,
                failure_reason=reason,
            )
            if not changed:
                # Кто-то успел раньше — вторую проводку не пишем.
                log.info('refund_lost_race')
                return False

            # Обязательство выдать товар превращается в обязательство вернуть
            # деньги, а затем гасится фактическим уходом денег.
            await self._ledger.record_double_entry(
                order_id=item.order_id,
                order_item_id=item.id,
                debit_account=LedgerAccount.CUSTOMER_LIABILITY,
                credit_account=LedgerAccount.REFUND_PAYABLE,
                amount=item.price,
                currency=item.currency,
                ref_type='refund',
                ref_id=refund_id,
                idempotency_key=f'{item.id}:refunded',
            )
            await self._ledger.record_double_entry(
                order_id=item.order_id,
                order_item_id=item.id,
                debit_account=LedgerAccount.REFUND_PAYABLE,
                credit_account=LedgerAccount.CASH_OUT,
                amount=item.price,
                currency=item.currency,
                ref_type='refund',
                ref_id=refund_id,
                idempotency_key=f'{item.id}:refund_paid',
            )
            await self._outbox.enqueue(
                NewCommandDTO(
                    kind=OutboxCommandKind.SETTLE_ORDER,
                    dedup_key=item.order_id,
                    payload={'order_id': item.order_id},
                    priority=PRIORITY_PAID,
                )
            )

        log.info('refund_done', amount=item.price, refund_id=refund_id)
        return True


class SettleOrderInteractor:
    """Закрытие заказа по итогам позиций.

    Считает не «сколько раз вызвали», а текущий срез позиций, поэтому
    повторный вызов ничего не меняет. Заказ закрывается ровно один раз —
    условным UPDATE, как и все прочие переходы.
    """

    def __init__(
        self,
        uow: UoW,
        orders: OrderRepository,
        items: OrderItemRepository,
        ledger: LedgerRepository,
        logger: PaymentsLogger,
    ) -> None:
        self._uow = uow
        self._orders = orders
        self._items = items
        self._ledger = ledger
        self._logger = logger

    async def execute(self, order_id: str) -> OrderStatus | None:
        log = self._logger.bind(order_id=order_id)

        async with self._uow:
            order = await self._orders.lock_by_id(order_id)
            if order is None:
                log.warning('settle_order_not_found')
                return None

            counts = await self._items.status_counts(order_id)
            if not counts.all_settled:
                log.info(
                    'settle_postponed',
                    settled=counts.settled,
                    total=counts.total,
                )
                return None

            target = _target_status(counts)
            changed = await self._orders.try_transition(
                order_id, target, sources_for(target)
            )
            if not changed:
                log.info('settle_already_done', status=order.status.value)
                return OrderStatus(order.status)

            paid, delivered, refunded = await self._ledger.order_settlement(order_id)

        if paid != delivered + refunded:
            # Единственная проверка, которой стоит верить: она читает деньги,
            # а не статусы. Расхождение — дефект, и молчать о нём нельзя.
            log.error(
                'settlement_money_mismatch',
                paid=paid,
                delivered=delivered,
                refunded=refunded,
            )
        else:
            log.info(
                'settle_done',
                status=target.value,
                paid=paid,
                delivered=delivered,
                refunded=refunded,
            )
        return target


def _target_status(counts: ItemStatusCountsDTO) -> OrderStatus:
    if counts.refunded == 0:
        return OrderStatus.DELIVERED
    if counts.delivered == 0:
        return OrderStatus.REFUNDED
    return OrderStatus.PARTIALLY_DELIVERED
