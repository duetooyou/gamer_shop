from dataclasses import asdict

from dishka import FromDishka
from dishka.integrations.litestar import inject
from litestar import Controller, get, post
from litestar.params import FromPath, Parameter

from gamer_shop.application.interactors import (
    DeliverOrderInteractor,
    ReconciliationInteractor,
    RefillStockInteractor,
    RetryStuckOrdersInteractor,
)
from gamer_shop.presentation.request_models import RefillStockIn
from gamer_shop.presentation.response_models import ReconciliationOut


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
            ledger_is_balanced=report.ledger_is_balanced,
            stock_drift=report.stock_drift,
        )

    @post('/orders/{order_id:str}/retry-delivery', summary='Дожать выдачу по заказу вручную')
    @inject
    async def retry_delivery(
        self, order_id: FromPath[str], interactor: FromDishka[DeliverOrderInteractor]
    ) -> dict:
        result = await interactor.execute(order_id)
        return {
            'order_id': result.order_id,
            'result': result.kind.value,
            'code': result.code,
            'supplier': result.supplier,
            'reason': result.reason,
        }

    @post('/retry-stuck', summary='Прогнать фоновое дожатие зависших заказов немедленно')
    @inject
    async def retry_stuck(self, interactor: FromDishka[RetryStuckOrdersInteractor]) -> dict:
        results = await interactor.execute()
        return {
            'processed': len(results),
            'outcomes': [
                {'order_id': r.order_id, 'result': r.kind.value} for r in results
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
