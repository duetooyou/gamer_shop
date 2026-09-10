"""Автовыдача кода по позиции заказа.

Три фазы, между которыми транзакция не удерживается: заявка, поход к
поставщику, фиксация. Держать блокировку строки на время HTTP-запроса нельзя —
он может зависнуть на десятки секунд.

Единица выдачи — позиция, а не заказ: в одном заказе позиции идут к разным
поставщикам и завершаются независимо друг от друга.
"""

import asyncio
import time

from gamer_shop.application.dto import (
    DeliveryResultDTO,
    DeliveryResultKind,
    NewCommandDTO,
    NewDiscrepancyDTO,
    OrderItemDTO,
    SupplierOutcome,
    SupplierOutcomeKind,
)
from gamer_shop.application.enums import (
    DELIVERABLE_ITEM_STATUSES,
    PRIORITY_PAID,
    DiscrepancyKind,
    LedgerAccount,
    OrderItemStatus,
    OrderStatus,
    OutboxCommandKind,
    SupplierName,
    SupplierRequestState,
    fallback_for,
)
from gamer_shop.application.exceptions import (
    DeliveryNotFinishedException,
    SupplierThrottledException,
)
from gamer_shop.application.interfaces import DeliveryLogger, SupplierClient, UoW
from gamer_shop.application.policies import build_request_id
from gamer_shop.application.repositories import (
    DeliveryRepository,
    DiscrepancyRepository,
    LedgerRepository,
    OrderItemRepository,
    OrderRepository,
    OutboxRepository,
    StockRepository,
    SupplierRateLimitRepository,
    SupplierRequestRepository,
)

# Код занят другой позицией. Непригоден навсегда: request_id детерминирован,
# поставщик вернёт его же на любом повторе.
CODE_ALREADY_USED = 'code_already_used'

# Ответ подписан не тем request_id, который отправляли.
FOREIGN_RESPONSE = 'foreign_response'


class DeliverOrderItemInteractor:
    def __init__(
        self,
        uow: UoW,
        orders: OrderRepository,
        items: OrderItemRepository,
        deliveries: DeliveryRepository,
        requests: SupplierRequestRepository,
        stock: StockRepository,
        ledger: LedgerRepository,
        outbox: OutboxRepository,
        discrepancies: DiscrepancyRepository,
        limits: SupplierRateLimitRepository,
        client: SupplierClient,
        logger: DeliveryLogger,
        stale_after_seconds: int,
        max_attempts_before_refund: int,
        permit_wait_seconds: float,
    ) -> None:
        self._uow = uow
        self._orders = orders
        self._items = items
        self._deliveries = deliveries
        self._requests = requests
        self._stock = stock
        self._ledger = ledger
        self._outbox = outbox
        self._discrepancies = discrepancies
        self._limits = limits
        self._client = client
        self._logger = logger
        self._stale_after_seconds = stale_after_seconds
        self._max_attempts_before_refund = max_attempts_before_refund
        self._permit_wait_seconds = permit_wait_seconds

    async def execute(self, item_id: str) -> DeliveryResultDTO:
        log = self._logger.bind(order_item_id=item_id)

        item = await self._items.get_by_id(item_id)
        if item is None:
            log.warning('delivery_item_not_found')
            return DeliveryResultDTO(DeliveryResultKind.SKIPPED, item_id)

        # Разрешение берётся до заявки, а не перед самим запросом. Позиция,
        # уже переведённая в delivering, упершись в лимит, застряла бы в
        # выдаче до фонового перехвата — всплеск растянулся бы на минуты
        # вместо секунд. Здесь же отказ ничего не ломает: команда просто
        # возвращается в очередь.
        await self._take_permit(item.supplier, log, wait=False)

        claim = await self._claim(item_id, log)
        if claim is None:
            return DeliveryResultDTO(DeliveryResultKind.SKIPPED, item_id)
        item, already = claim
        if already is not None:
            if (
                already.kind is DeliveryResultKind.OUT_OF_STOCK
                and self._attempts_left(item)
            ):
                # Остаток мог пополниться — просим релей повторить.
                raise DeliveryNotFinishedException(
                    'остатка нет', details={'order_item_id': item.id}
                )
            return already

        outcome = await self._ask_suppliers(item, log)

        if outcome.kind is SupplierOutcomeKind.OK and outcome.code:
            return await self._finalize_delivered(item, outcome, log)

        if outcome.kind is SupplierOutcomeKind.UNKNOWN:
            # Судьба кода неизвестна. Позиция остаётся в delivering, резерв
            # держим, деньги не трогаем. Дожмёт повтор — тем же request_id.
            log.warning(
                'delivery_pending_unknown',
                supplier=outcome.supplier.value,
                request_id=outcome.request_id,
                reason=outcome.reason,
            )
            raise DeliveryNotFinishedException(
                'судьба кода неизвестна',
                details={'order_item_id': item.id, 'reason': outcome.reason},
            )

        return await self._finalize_refused(item, outcome, log)

    async def _claim(
        self, item_id: str, log
    ) -> tuple[OrderItemDTO, DeliveryResultDTO | None] | None:
        """Взять право на выдачу. None — позиция занята или выдавать нечего."""
        async with self._uow:
            item = await self._items.get_by_id(item_id)
            if item is None:
                log.warning('delivery_item_not_found')
                return None

            existing = await self._deliveries.get_by_item(item_id)
            if existing is not None:
                # Дотягиваем статус на случай падения между записью выдачи
                # и переходом.
                await self._settle_delivered(item)
                log.info('delivery_already_done', code_present=True)
                return item, DeliveryResultDTO(
                    DeliveryResultKind.ALREADY_DELIVERED,
                    item_id,
                    code=existing.code,
                    supplier=existing.supplier,
                )

            # Условный UPDATE и есть взаимное исключение: выигрывает один вызов.
            fresh = await self._items.try_transition(
                item_id, OrderItemStatus.DELIVERING, DELIVERABLE_ITEM_STATUSES
            )
            if fresh:
                if not await self._stock.reserve(item.sku):
                    await self._items.try_transition(
                        item_id,
                        OrderItemStatus.OUT_OF_STOCK,
                        frozenset({OrderItemStatus.DELIVERING}),
                        failure_reason='out_of_stock',
                    )
                    if not self._attempts_left(item):
                        await self._enqueue_refund(item, 'out_of_stock')
                    log.warning('delivery_out_of_stock', sku=item.sku, stage='reserve')
                    # Исключение — за границей транзакции: брошенное здесь,
                    # оно откатило бы сам переход в out_of_stock.
                    return item, DeliveryResultDTO(
                        DeliveryResultKind.OUT_OF_STOCK, item_id, reason='out_of_stock'
                    )
                # Заказ показывается в работе. Условный UPDATE: срабатывает
                # на первой позиции, остальные его не трогают.
                await self._orders.try_transition(
                    item.order_id, OrderStatus.DELIVERING, frozenset({OrderStatus.PAID})
                )
                log.info('delivery_claimed', sku=item.sku, claim='fresh')
                return item, None

            # Уже в delivering: перехватываем, только если завис. Резерв при
            # этом сделан предыдущей попыткой, повторно не берём.
            if await self._items.try_reclaim_stale_delivering(
                item_id, self._stale_after_seconds
            ):
                log.info('delivery_claimed', sku=item.sku, claim='reclaim_stale')
                return item, None

            log.info('delivery_skipped', status=item.status.value)
            return None

    async def _ask_suppliers(self, item: OrderItemDTO, log) -> SupplierOutcome:
        """Поставщик позиции, затем резервный.

        Фолбэк разрешён только после отказа по существу. Таймаут обход
        прекращает: поставщик мог успеть выдать код, и второй выдал бы ещё один.
        """
        order: tuple[SupplierName, ...] = (item.supplier, fallback_for(item.supplier))
        last = SupplierOutcome(
            kind=SupplierOutcomeKind.REFUSED,
            request_id='',
            supplier=order[0],
            reason='no_supplier_tried',
        )
        for supplier in order:
            outcome = await self._resolve_with_supplier(
                item, supplier, log, permit_held=supplier is item.supplier
            )
            last = outcome
            if outcome.kind is SupplierOutcomeKind.OK:
                if not await self._reject_if_code_taken(item, outcome, log):
                    return outcome
                # Код занят — этот поставщик бесполезен, идём к следующему.
                last = SupplierOutcome(
                    kind=SupplierOutcomeKind.REFUSED,
                    request_id=outcome.request_id,
                    supplier=supplier,
                    reason=CODE_ALREADY_USED,
                )
                continue
            if outcome.kind is SupplierOutcomeKind.UNKNOWN:
                log.warning(
                    'fallback_blocked_by_unknown',
                    supplier=supplier.value,
                    request_id=outcome.request_id,
                    reason=outcome.reason,
                )
                return outcome
            log.info(
                'supplier_refused_trying_next',
                supplier=supplier.value,
                reason=outcome.reason,
            )
        return last

    async def _reject_if_code_taken(
        self, item: OrderItemDTO, outcome: SupplierOutcome, log
    ) -> bool:
        """Пометить обращение непригодным, если код занят другой позицией."""
        async with self._uow:
            taken = await self._deliveries.code_taken_by_other_item(outcome.code, item.id)
            if taken:
                await self._requests.save_outcome(
                    SupplierOutcome(
                        kind=SupplierOutcomeKind.REFUSED,
                        request_id=outcome.request_id,
                        supplier=outcome.supplier,
                        reason=CODE_ALREADY_USED,
                    )
                )
        if taken:
            await self._record(
                DiscrepancyKind.DUPLICATE_CODE,
                item,
                outcome.supplier,
                outcome.request_id,
                code=outcome.code,
                detail='код уже закреплён за другой позицией',
                resolution='обращение помечено непригодным, позиция уходит к резервному',
            )
            log.warning(
                'supplier_code_already_used',
                supplier=outcome.supplier.value,
                request_id=outcome.request_id,
            )
        return taken

    async def _resolve_with_supplier(
        self, item: OrderItemDTO, supplier: SupplierName, log, permit_held: bool
    ) -> SupplierOutcome:
        request_id = build_request_id(item.id, supplier)

        async with self._uow:
            known = await self._requests.get_or_create(
                item.id, supplier, item.sku, request_id
            )
        if known.state is SupplierRequestState.OK and known.code:
            # Код уже получен — второй раз не спрашиваем, иначе выдач будет две.
            log.info('supplier_code_known', supplier=supplier.value, request_id=request_id)
            return SupplierOutcome(
                kind=SupplierOutcomeKind.OK,
                request_id=request_id,
                supplier=supplier,
                code=known.code,
            )
        if (
            known.state is SupplierRequestState.REFUSED
            and known.reason == CODE_ALREADY_USED
        ):
            # Поставщик вернёт тот же занятый код — переспрашивать незачем.
            log.warning(
                'supplier_code_unusable', supplier=supplier.value, request_id=request_id
            )
            return SupplierOutcome(
                kind=SupplierOutcomeKind.REFUSED,
                request_id=request_id,
                supplier=supplier,
                reason=CODE_ALREADY_USED,
            )

        # Прочие прошлые отказы повтор не блокируют: кода не выдали,
        # а остаток мог быть пополнен.
        if not permit_held:
            # У резервного поставщика своё ведро, и разрешения здесь ждут:
            # позиция уже в выдаче, бросать её на полпути дороже.
            await self._take_permit(supplier, log, wait=True)
        outcome = await self._client.issue(supplier, request_id, item.sku, item.id)
        if outcome.kind is SupplierOutcomeKind.UNKNOWN:
            outcome = await self._verify(item, supplier, request_id, outcome, log)

        async with self._uow:
            await self._requests.save_outcome(outcome)
        return outcome

    async def _verify(
        self,
        item: OrderItemDTO,
        supplier: SupplierName,
        request_id: str,
        outcome: SupplierOutcome,
        log,
    ) -> SupplierOutcome:
        """Установить, что поставщик выдал на самом деле.

        Ответу на выдачу веры нет: «ошибка» может означать выданный код, а
        200 — чужой. Запрос состояния — единственное, что различает «не
        выдал» и «выдал и соврал», и без него фолбэк небезопасен.
        """
        if outcome.reason == FOREIGN_RESPONSE:
            await self._record(
                DiscrepancyKind.FOREIGN_RESPONSE,
                item,
                supplier,
                request_id,
                detail='ответ подписан чужим request_id',
                resolution='ответ отброшен, состояние выяснено запросом состояния',
            )

        # Запрос состояния — тоже обращение к поставщику и тоже под лимитом.
        await self._take_permit(supplier, log, wait=True)
        probed = await self._client.probe(supplier, request_id)

        if probed.kind is SupplierOutcomeKind.OK and probed.code:
            if outcome.answered and outcome.reason != FOREIGN_RESPONSE:
                # Поставщик сказал «ошибка», а код выдал. Оборванная связь
                # сюда не попадает: молчание — не ложь.
                await self._record(
                    DiscrepancyKind.PHANTOM_ISSUE,
                    item,
                    supplier,
                    request_id,
                    code=probed.code,
                    detail=f'ответ на выдачу: {outcome.reason}',
                    resolution='код найден запросом состояния и отдан позиции',
                )
            log.warning(
                'supplier_lied_about_failure',
                supplier=supplier.value,
                request_id=request_id,
                reason=outcome.reason,
            )
            return SupplierOutcome(
                kind=SupplierOutcomeKind.OK,
                request_id=request_id,
                supplier=supplier,
                code=probed.code,
            )

        if probed.kind is SupplierOutcomeKind.REFUSED:
            # Правда установлена: кода нет. Теперь фолбэк безопасен.
            return SupplierOutcome(
                kind=SupplierOutcomeKind.REFUSED,
                request_id=request_id,
                supplier=supplier,
                reason=outcome.reason,
            )

        # Правды не узнали — остаёмся в неопределённости, фолбэк запрещён.
        return outcome

    async def _take_permit(
        self, supplier: SupplierName, log, *, wait: bool
    ) -> None:
        """Разрешение на одно обращение к поставщику.

        Отказ — не провал выдачи, а отсрочка: команда вернётся в очередь на
        срок до следующего разрешения. Ждём только там, где отступить уже
        нельзя, — у резервного поставщика и при запросе состояния.

        Неизрасходованное разрешение обратно не возвращается: если поход к
        поставщику в итоге не состоялся, токен просто дольше доливается в
        ведро. Возврат токена — лишняя запись на горячем пути ради экономии,
        которой никто не заметит, а лимит от неё только строже.
        """
        deadline = time.monotonic() + (self._permit_wait_seconds if wait else 0.0)
        while True:
            async with self._uow:
                decision = await self._limits.acquire(supplier)
            if decision.granted:
                return

            left = deadline - time.monotonic()
            if left <= 0:
                log.info(
                    'supplier_throttled',
                    supplier=supplier.value,
                    retry_after=round(decision.retry_after_seconds, 3),
                )
                raise SupplierThrottledException(
                    details={'supplier': supplier.value},
                    retry_after_seconds=decision.retry_after_seconds,
                )
            await asyncio.sleep(min(decision.retry_after_seconds, left))

    async def _record(
        self,
        kind: DiscrepancyKind,
        item: OrderItemDTO,
        supplier: SupplierName,
        request_id: str,
        code: str | None = None,
        detail: str | None = None,
        resolution: str | None = None,
    ) -> None:
        async with self._uow:
            await self._discrepancies.record(
                NewDiscrepancyDTO(
                    kind=kind,
                    supplier=supplier,
                    request_id=request_id,
                    order_item_id=item.id,
                    code=code,
                    detail=detail,
                    resolution=resolution,
                )
            )

    async def _finalize_delivered(
        self, item: OrderItemDTO, outcome: SupplierOutcome, log
    ) -> DeliveryResultDTO:
        async with self._uow:
            created = await self._deliveries.create_if_absent(
                item.id, outcome.code, outcome.supplier.value, outcome.request_id
            )
            if not created:
                # Уникальный индекс: выдача по позиции уже есть либо код занят.
                existing = await self._deliveries.get_by_item(item.id)
                log.warning(
                    'delivery_duplicate_blocked',
                    request_id=outcome.request_id,
                    existing_code_present=existing is not None,
                )
                if existing is None:
                    # Выдачи нет — значит конфликт по коду.
                    await self._requests.save_outcome(
                        SupplierOutcome(
                            kind=SupplierOutcomeKind.REFUSED,
                            request_id=outcome.request_id,
                            supplier=outcome.supplier,
                            reason=CODE_ALREADY_USED,
                        )
                    )
                    await self._discrepancies.record(
                        NewDiscrepancyDTO(
                            kind=DiscrepancyKind.UNUSABLE_CODE,
                            supplier=outcome.supplier,
                            request_id=outcome.request_id,
                            order_item_id=item.id,
                            code=outcome.code,
                            detail='код занят: уникальный индекс не пропустил выдачу',
                            resolution='обращение помечено непригодным',
                        )
                    )
                    await self._stock.release(item.sku)
                    await self._items.try_transition(
                        item.id,
                        OrderItemStatus.DELIVERY_FAILED,
                        frozenset({OrderItemStatus.DELIVERING}),
                        failure_reason=CODE_ALREADY_USED,
                    )
                    await self._enqueue_refund(item, CODE_ALREADY_USED)
                    return DeliveryResultDTO(
                        DeliveryResultKind.DELIVERY_FAILED,
                        item.id,
                        reason=CODE_ALREADY_USED,
                    )
                await self._settle_delivered(item)
                return DeliveryResultDTO(
                    DeliveryResultKind.ALREADY_DELIVERED,
                    item.id,
                    code=existing.code,
                    supplier=existing.supplier,
                )

            await self._items.try_transition(
                item.id,
                OrderItemStatus.DELIVERED,
                frozenset({OrderItemStatus.DELIVERING}),
            )
            await self._stock.commit_reserved(item.sku)
            await self._ledger.record_double_entry(
                order_id=item.order_id,
                order_item_id=item.id,
                debit_account=LedgerAccount.CUSTOMER_LIABILITY,
                credit_account=LedgerAccount.REVENUE,
                amount=item.price,
                currency=item.currency,
                ref_type='delivery',
                ref_id=outcome.request_id,
                idempotency_key=f'{item.id}:delivered',
            )
            await self._enqueue_settlement(item)

        log.info(
            'delivery_done',
            supplier=outcome.supplier.value,
            request_id=outcome.request_id,
            attempts=outcome.attempts,
        )
        return DeliveryResultDTO(
            DeliveryResultKind.DELIVERED,
            item.id,
            code=outcome.code,
            supplier=outcome.supplier.value,
        )

    async def _finalize_refused(
        self, item: OrderItemDTO, outcome: SupplierOutcome, log
    ) -> DeliveryResultDTO:
        out_of_stock = (outcome.reason or '').find('out_of_stock') >= 0
        target = (
            OrderItemStatus.OUT_OF_STOCK if out_of_stock else OrderItemStatus.DELIVERY_FAILED
        )
        kind = (
            DeliveryResultKind.OUT_OF_STOCK
            if out_of_stock
            else DeliveryResultKind.DELIVERY_FAILED
        )

        async with self._uow:
            # Выдачи не было — резерв возвращаем в остаток.
            await self._stock.release(item.sku)
            await self._items.try_transition(
                item.id,
                target,
                frozenset({OrderItemStatus.DELIVERING}),
                failure_reason=outcome.reason,
            )
            if not self._attempts_left(item):
                await self._enqueue_refund(item, outcome.reason)

        log.warning(
            'delivery_refused',
            new_status=target.value,
            reason=outcome.reason,
            supplier=outcome.supplier.value,
            attempts=item.delivery_attempts + 1,
        )
        if self._attempts_left(item):
            raise DeliveryNotFinishedException(
                outcome.reason or 'поставщик отказал',
                details={'order_item_id': item.id},
            )
        return DeliveryResultDTO(kind, item.id, reason=outcome.reason)

    async def _settle_delivered(self, item: OrderItemDTO) -> None:
        """Дотянуть статус позиции и попросить расчёт заказа."""
        await self._items.try_transition(
            item.id, OrderItemStatus.DELIVERED, frozenset({OrderItemStatus.DELIVERING})
        )
        await self._enqueue_settlement(item)

    async def _enqueue_settlement(self, item: OrderItemDTO) -> None:
        await self._outbox.enqueue(
            NewCommandDTO(
                kind=OutboxCommandKind.SETTLE_ORDER,
                dedup_key=item.order_id,
                payload={'order_id': item.order_id},
                priority=PRIORITY_PAID,
            )
        )

    def _attempts_left(self, item: OrderItemDTO) -> bool:
        # Попытка этого прохода уже учтена переходом в delivering.
        return item.delivery_attempts + 1 < self._max_attempts_before_refund

    async def _enqueue_refund(self, item: OrderItemDTO, reason: str | None) -> None:
        """Обменять неудавшуюся выдачу на деньги.

        Решение принимается здесь, а не в релее: релей не знает, что за
        невыданный товар полагается возврат, — для него это просто отказ.
        """
        await self._outbox.enqueue(
            NewCommandDTO(
                kind=OutboxCommandKind.REFUND_ITEM,
                dedup_key=item.id,
                payload={'order_item_id': item.id, 'reason': reason or 'delivery_failed'},
                priority=PRIORITY_PAID,
            )
        )
