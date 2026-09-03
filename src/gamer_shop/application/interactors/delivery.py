"""Автовыдача кода.

Три фазы, между которыми транзакция не удерживается: заявка, поход к
поставщику, фиксация. Держать блокировку строки на время HTTP-запроса нельзя —
он может зависнуть на десятки секунд.
"""

from gamer_shop.application.dto import (
    DeliveryResultDTO,
    DeliveryResultKind,
    OrderDTO,
    SupplierOutcome,
    SupplierOutcomeKind,
)
from gamer_shop.application.enums import (
    DELIVERABLE_STATUSES,
    LedgerAccount,
    OrderStatus,
    SupplierName,
    SupplierRequestState,
)
from gamer_shop.application.interfaces import DeliveryLogger, SupplierClient, UoW
from gamer_shop.application.policies import build_request_id
from gamer_shop.application.repositories import (
    DeliveryRepository,
    LedgerRepository,
    OrderRepository,
    StockRepository,
    SupplierRequestRepository,
)

SUPPLIER_ORDER: tuple[SupplierName, ...] = (SupplierName.A, SupplierName.B)

# Код занят другим заказом. Непригоден навсегда: request_id детерминирован,
# поставщик вернёт его же на любом повторе.
CODE_ALREADY_USED = 'code_already_used'


class DeliverOrderInteractor:
    def __init__(
        self,
        uow: UoW,
        orders: OrderRepository,
        deliveries: DeliveryRepository,
        requests: SupplierRequestRepository,
        stock: StockRepository,
        ledger: LedgerRepository,
        client: SupplierClient,
        logger: DeliveryLogger,
        stale_after_seconds: int,
    ) -> None:
        self._uow = uow
        self._orders = orders
        self._deliveries = deliveries
        self._requests = requests
        self._stock = stock
        self._ledger = ledger
        self._client = client
        self._logger = logger
        self._stale_after_seconds = stale_after_seconds

    async def execute(self, order_id: str) -> DeliveryResultDTO:
        log = self._logger.bind(order_id=order_id)

        claim = await self._claim(order_id, log)
        if claim is None:
            return DeliveryResultDTO(DeliveryResultKind.SKIPPED, order_id)
        order, already = claim
        if already is not None:
            return already

        outcome = await self._ask_suppliers(order, log)

        if outcome.kind is SupplierOutcomeKind.OK and outcome.code:
            return await self._finalize_delivered(order, outcome, log)

        if outcome.kind is SupplierOutcomeKind.UNKNOWN:
            # Судьба кода неизвестна. Заказ остаётся в `delivering`, резерв держим,
            # деньги не трогаем. Дожмёт фоновая задача — тем же request_id.
            log.warning(
                'delivery_pending_unknown',
                supplier=outcome.supplier.value,
                request_id=outcome.request_id,
                reason=outcome.reason,
            )
            return DeliveryResultDTO(
                DeliveryResultKind.PENDING_UNKNOWN, order.id, reason=outcome.reason
            )

        return await self._finalize_refused(order, outcome, log)

    async def _claim(
        self, order_id: str, log
    ) -> tuple[OrderDTO, DeliveryResultDTO | None] | None:
        """Взять право на выдачу. None — заказ занят или выдавать нечего."""
        async with self._uow:
            order = await self._orders.get_by_id(order_id)
            if order is None:
                log.warning('delivery_order_not_found')
                return None

            existing = await self._deliveries.get_by_order(order_id)
            if existing is not None:
                # Дотягиваем статус на случай падения между записью выдачи и переходом.
                await self._orders.try_transition(
                    order_id, OrderStatus.DELIVERED, frozenset({OrderStatus.DELIVERING})
                )
                log.info('delivery_already_done', code_present=True)
                return order, DeliveryResultDTO(
                    DeliveryResultKind.ALREADY_DELIVERED,
                    order_id,
                    code=existing.code,
                    supplier=existing.supplier,
                )

            # Условный UPDATE и есть взаимное исключение: выигрывает один вызов.
            fresh = await self._orders.try_transition(
                order_id, OrderStatus.DELIVERING, DELIVERABLE_STATUSES
            )
            if fresh:
                if not await self._stock.reserve(order.sku):
                    await self._orders.try_transition(
                        order_id,
                        OrderStatus.OUT_OF_STOCK,
                        frozenset({OrderStatus.DELIVERING}),
                        failure_reason='out_of_stock',
                    )
                    log.warning('delivery_out_of_stock', sku=order.sku, stage='reserve')
                    return order, DeliveryResultDTO(
                        DeliveryResultKind.OUT_OF_STOCK, order_id, reason='out_of_stock'
                    )
                log.info('delivery_claimed', sku=order.sku, claim='fresh')
                return order, None

            # Уже в delivering: перехватываем, только если завис. Резерв при
            # этом сделан предыдущей попыткой, повторно не берём.
            if await self._orders.try_reclaim_stale_delivering(
                order_id, self._stale_after_seconds
            ):
                log.info('delivery_claimed', sku=order.sku, claim='reclaim_stale')
                return order, None

            log.info('delivery_skipped', status=order.status.value)
            return None

    async def _ask_suppliers(self, order: OrderDTO, log) -> SupplierOutcome:
        """Обход поставщиков.

        Фолбэк разрешён только после отказа по существу. Таймаут обход
        прекращает: поставщик мог успеть выдать код, и второй выдал бы ещё один.
        """
        last = SupplierOutcome(
            kind=SupplierOutcomeKind.REFUSED,
            request_id='',
            supplier=SUPPLIER_ORDER[0],
            reason='no_supplier_tried',
        )
        for supplier in SUPPLIER_ORDER:
            outcome = await self._resolve_with_supplier(order, supplier, log)
            last = outcome
            if outcome.kind is SupplierOutcomeKind.OK:
                if not await self._reject_if_code_taken(order, outcome, log):
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
        self, order: OrderDTO, outcome: SupplierOutcome, log
    ) -> bool:
        """Пометить обращение непригодным, если код занят другим заказом."""
        async with self._uow:
            taken = await self._deliveries.code_taken_by_other_order(
                outcome.code, order.id
            )
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
            log.warning(
                'supplier_code_already_used',
                supplier=outcome.supplier.value,
                request_id=outcome.request_id,
            )
        return taken

    async def _resolve_with_supplier(
        self, order: OrderDTO, supplier: SupplierName, log
    ) -> SupplierOutcome:
        request_id = build_request_id(order.id, supplier)

        async with self._uow:
            known = await self._requests.get_or_create(
                order.id, supplier, order.sku, request_id
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
        outcome = await self._client.issue(supplier, request_id, order.sku, order.id)

        async with self._uow:
            await self._requests.save_outcome(outcome)
        return outcome

    async def _finalize_delivered(
        self, order: OrderDTO, outcome: SupplierOutcome, log
    ) -> DeliveryResultDTO:
        async with self._uow:
            created = await self._deliveries.create_if_absent(
                order.id, outcome.code, outcome.supplier.value, outcome.request_id
            )
            if not created:
                # Уникальный индекс: выдача по заказу уже есть либо код занят.
                existing = await self._deliveries.get_by_order(order.id)
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
                    await self._stock.release(order.sku)
                    await self._orders.try_transition(
                        order.id,
                        OrderStatus.DELIVERY_FAILED,
                        frozenset({OrderStatus.DELIVERING}),
                        failure_reason=CODE_ALREADY_USED,
                    )
                    return DeliveryResultDTO(
                        DeliveryResultKind.DELIVERY_FAILED,
                        order.id,
                        reason=CODE_ALREADY_USED,
                    )
                await self._orders.try_transition(
                    order.id, OrderStatus.DELIVERED, frozenset({OrderStatus.DELIVERING})
                )
                return DeliveryResultDTO(
                    DeliveryResultKind.ALREADY_DELIVERED,
                    order.id,
                    code=existing.code,
                    supplier=existing.supplier,
                )

            await self._orders.try_transition(
                order.id, OrderStatus.DELIVERED, frozenset({OrderStatus.DELIVERING})
            )
            await self._stock.commit_reserved(order.sku)
            await self._ledger.record_double_entry(
                order_id=order.id,
                debit_account=LedgerAccount.CUSTOMER_LIABILITY,
                credit_account=LedgerAccount.REVENUE,
                amount=order.price,
                currency=order.currency,
                ref_type='delivery',
                ref_id=outcome.request_id,
                idempotency_key=f'{order.id}:delivered',
            )

        log.info(
            'delivery_done',
            supplier=outcome.supplier.value,
            request_id=outcome.request_id,
            attempts=outcome.attempts,
        )
        return DeliveryResultDTO(
            DeliveryResultKind.DELIVERED,
            order.id,
            code=outcome.code,
            supplier=outcome.supplier.value,
        )

    async def _finalize_refused(
        self, order: OrderDTO, outcome: SupplierOutcome, log
    ) -> DeliveryResultDTO:
        out_of_stock = (outcome.reason or '').find('out_of_stock') >= 0
        target = OrderStatus.OUT_OF_STOCK if out_of_stock else OrderStatus.DELIVERY_FAILED
        kind = (
            DeliveryResultKind.OUT_OF_STOCK
            if out_of_stock
            else DeliveryResultKind.DELIVERY_FAILED
        )

        async with self._uow:
            # Выдачи не было — резерв возвращаем в остаток.
            await self._stock.release(order.sku)
            await self._orders.try_transition(
                order.id,
                target,
                frozenset({OrderStatus.DELIVERING}),
                failure_reason=outcome.reason,
            )

        log.warning(
            'delivery_refused',
            new_status=target.value,
            reason=outcome.reason,
            supplier=outcome.supplier.value,
        )
        return DeliveryResultDTO(kind, order.id, reason=outcome.reason)
