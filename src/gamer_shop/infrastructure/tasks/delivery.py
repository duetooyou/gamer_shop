"""Фоновые задачи выдачи и сверки."""

from datetime import timedelta

from dishka.integrations.taskiq import FromDishka, inject

from gamer_shop.application.interactors import (
    ApplyOrphanEventsInteractor,
    DeliverOrderInteractor,
    ReconciliationInteractor,
    RetryStuckOrdersInteractor,
)
from gamer_shop.application.interfaces import DeliveryLogger
from gamer_shop.infrastructure.config import Config
from gamer_shop.infrastructure.taskiq_base.broker import broker

# Читается на импорте: taskiq разбирает расписание из декоратора при
# регистрации задачи, до того как поднимется контейнер.
_delivery = Config().delivery
_SCAN_INTERVAL = timedelta(seconds=_delivery.scan_interval_seconds)
_REPORT_INTERVAL = timedelta(seconds=_delivery.scan_interval_seconds * 10)


@broker.task(task_name='deliver_order')
@inject(patch_module=True)
async def deliver_order_task(
    order_id: str, interactor: FromDishka[DeliverOrderInteractor]
) -> str:
    """Защита от повторной выдачи живёт в интеракторе, поэтому повторная
    постановка той же задачи безопасна."""
    result = await interactor.execute(order_id)
    return result.kind.value


@broker.task(task_name='retry_stuck_orders', schedule=[{'interval': _SCAN_INTERVAL}])
@inject(patch_module=True)
async def retry_stuck_orders_task(
    interactor: FromDishka[RetryStuckOrdersInteractor],
) -> int:
    """Дожатие «зависших» заказов."""
    return len(await interactor.execute())


@broker.task(task_name='apply_orphan_events', schedule=[{'interval': _SCAN_INTERVAL}])
@inject(patch_module=True)
async def apply_orphan_events_task(
    interactor: FromDishka[ApplyOrphanEventsInteractor],
) -> int:
    """Вебхуки, пришедшие раньше заказа."""
    return len(await interactor.execute())


@broker.task(task_name='reconciliation_report', schedule=[{'interval': _REPORT_INTERVAL}])
@inject(patch_module=True)
async def reconciliation_report_task(
    interactor: FromDishka[ReconciliationInteractor],
    logger: FromDishka[DeliveryLogger],
) -> bool:
    """Сверка в лог: расхождения видны без ручного запроса."""
    report = await interactor.execute()
    logger.info(
        'reconciliation_report',
        paid_not_delivered=len(report.paid_not_delivered),
        delivered_not_paid=len(report.delivered_not_paid),
        stuck_delivering=len(report.stuck_delivering),
        unresolved_supplier_requests=len(report.unresolved_supplier_requests),
        unapplied_events=len(report.unapplied_events),
        ledger_is_balanced=report.ledger_is_balanced,
        stock_drift=report.stock_drift,
    )
    return report.ledger_is_balanced
