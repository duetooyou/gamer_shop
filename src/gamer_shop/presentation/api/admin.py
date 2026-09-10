from dataclasses import asdict
from typing import Annotated

from dishka import FromDishka
from dishka.integrations.litestar import inject
from litestar import Controller, get, post
from litestar.params import FromPath, Parameter, QueryParameter

from gamer_shop.application.interactors import (
    DeliverOrderItemInteractor,
    OrderStateAtInteractor,
    PeriodTotalsInteractor,
    ProgressInteractor,
    ReconciliationInteractor,
    RefillStockInteractor,
    RetryStuckOrdersInteractor,
    SettleOrderInteractor,
)
from gamer_shop.application.enums import TERMINAL_ITEM_STATUSES
from gamer_shop.application.exceptions import ApplicationException
from gamer_shop.application.repositories import OrderItemRepository
from gamer_shop.presentation.moments import parse_moment
from gamer_shop.presentation.request_models import RefillStockIn
from gamer_shop.presentation.response_models import (
    OrderStateAtOut,
    PeriodTotalsOut,
    ProgressOut,
    ReconciliationOut,
)


class AdminController(Controller):
    """Эксплуатация: сверка, ручное дожатие, пополнение остатка."""

    path = '/admin'
    tags = ['Сверка и эксплуатация']

    @get(
        '/reconciliation',
        summary='Отчёт сверки',
        description=(
            '«Оплачен, но не выдан», «выдан, но не оплачен», зависшие заказы, '
            'неразрешённые обращения к поставщику, непринятые вебхуки и баланс '
            'журнала денежных движений.'
        ),
    )
    @inject
    async def reconciliation(
        self,
        interactor: FromDishka[ReconciliationInteractor],
        older_than_seconds: int | None = Parameter(default=None, ge=0),
    ) -> ReconciliationOut:
        report = await interactor.execute(older_than_seconds)
        return ReconciliationOut(
            paid_not_delivered=[asdict(p) for p in report.paid_not_delivered],
            delivered_not_paid=[asdict(p) for p in report.delivered_not_paid],
            stuck_delivering=[asdict(p) for p in report.stuck_delivering],
            unresolved_supplier_requests=[
                asdict(p) for p in report.unresolved_supplier_requests
            ],
            unapplied_events=[asdict(p) for p in report.unapplied_events],
            ledger_balances=[
                {
                    'account': b.account,
                    'debit_total': b.debit_total,
                    'credit_total': b.credit_total,
                    'balance': b.balance,
                }
                for b in report.ledger_balances
            ],
            open_discrepancies=[
                {
                    'id': d.id,
                    'kind': d.kind,
                    'supplier': d.supplier,
                    'request_id': d.request_id,
                    'order_item_id': d.order_item_id,
                    'code': d.code,
                    'detail': d.detail,
                    'state': d.state,
                    'resolution': d.resolution,
                }
                for d in report.open_discrepancies
            ],
            discrepancy_counts=report.discrepancy_counts,
            money_mismatch=[asdict(p) for p in report.money_mismatch],
            ledger_is_balanced=report.ledger_is_balanced,
            stock_drift=report.stock_drift,
            outbox_depth=[
                {
                    'partition_key': d.partition_key,
                    'kind': d.kind,
                    'state': d.state,
                    'count': d.count,
                }
                for d in report.outbox_depth
            ],
            dead_commands=[
                {
                    'id': c.id,
                    'kind': c.kind,
                    'dedup_key': c.dedup_key,
                    'attempts': c.attempts,
                }
                for c in report.dead_commands
            ],
        )

    @post(
        '/orders/{order_id:str}/retry-delivery',
        summary='Дожать выдачу по всем незакрытым позициям заказа',
    )
    @inject
    async def retry_delivery(
        self,
        order_id: FromPath[str],
        items: FromDishka[OrderItemRepository],
        deliver: FromDishka[DeliverOrderItemInteractor],
        settle: FromDishka[SettleOrderInteractor],
    ) -> dict:
        results = []
        for item in await items.list_by_order(order_id):
            if item.status in TERMINAL_ITEM_STATUSES:
                continue
            try:
                result = await deliver.execute(item.id)
            except ApplicationException as exc:
                results.append(
                    {'order_item_id': item.id, 'result': 'pending', 'reason': str(exc)}
                )
                continue
            results.append(
                {
                    'order_item_id': result.order_item_id,
                    'result': result.kind.value,
                    'code': result.code,
                    'supplier': result.supplier,
                    'reason': result.reason,
                }
            )
        status = await settle.execute(order_id)
        return {
            'order_id': order_id,
            'order_status': status.value if status else None,
            'items': results,
        }

    @post('/retry-stuck', summary='Прогнать фоновое дожатие зависших заказов немедленно')
    @inject
    async def retry_stuck(self, interactor: FromDishka[RetryStuckOrdersInteractor]) -> dict:
        results = await interactor.execute()
        return {
            'processed': len(results),
            'outcomes': [
                {'order_item_id': r.order_item_id, 'result': r.kind.value}
                for r in results
            ],
        }

    @post('/stock/{sku:str}/refill', summary='Пополнить остаток по SKU')
    @inject
    async def refill(
        self, sku: FromPath[str], data: RefillStockIn,
        interactor: FromDishka[RefillStockInteractor]
    ) -> dict:
        available = await interactor.execute(sku, data.count)
        return {'sku': sku, 'available_count': available}

    @get(
        '/progress',
        summary='Прогресс разбора очереди',
        description=(
            'Сколько заказов и позиций стоит в очереди, сколько уже выдано и '
            'возвращено, что происходит с лимитом каждого поставщика. '
            'Считается по состоянию, поэтому перезапуск процессов картину '
            'не обнуляет.'
        ),
    )
    @inject
    async def progress(self, interactor: FromDishka[ProgressInteractor]) -> ProgressOut:
        return ProgressOut.model_validate(await interactor.execute())

    @get(
        '/orders/{order_id:str}/at',
        summary='Состояние заказа и денег на прошлый момент',
        description=(
            'Восстанавливается из журнала состояний и проводок — обоих только '
            'дополняемых. Без as_of отвечает на «сейчас». Момент, когда заказа '
            'ещё не было, — законный ответ с exists=false, а не 404.'
        ),
    )
    @inject
    async def order_state_at(
        self,
        order_id: FromPath[str],
        interactor: FromDishka[OrderStateAtInteractor],
        as_of: Annotated[
            str | None,
            QueryParameter(
                description=(
                    'Дата 2026-09-10 либо момент 2026-09-10T16:30:11Z. '
                    'Дата — это сутки, поэтому берётся их конец: в ответ '
                    'попадает весь день.'
                )
            ),
        ] = None,
        with_events: Annotated[bool, QueryParameter()] = False,
    ) -> OrderStateAtOut:
        snapshot = await interactor.execute(
            order_id, parse_moment(as_of, end_of_day=True), with_events
        )
        return OrderStateAtOut.model_validate(snapshot)

    @get(
        '/period-totals',
        summary='Итоги за период',
        description=(
            'Считаются из журналов. Сходимость — тождество двойной записи: '
            'оплачено за период равно выданному, возвращённому и приросту '
            'обязательства перед покупателем.'
        ),
    )
    @inject
    async def period_totals(
        self,
        interactor: FromDishka[PeriodTotalsInteractor],
        period_from: Annotated[
            str | None,
            QueryParameter(
                name='from',
                description='Дата берётся с начала суток, момент — как есть.',
            ),
        ] = None,
        period_to: Annotated[
            str | None,
            QueryParameter(
                name='to',
                description='Дата берётся с конца суток, момент — как есть.',
            ),
        ] = None,
    ) -> PeriodTotalsOut:
        return PeriodTotalsOut.model_validate(
            await interactor.execute(
                parse_moment(period_from),
                parse_moment(period_to, end_of_day=True),
            )
        )
