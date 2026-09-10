"""Разбор команд аутбокса по обработчикам.

Каждая команда исполняется в собственной области видимости DI: своя сессия,
своя транзакция. Соседняя упавшая команда не должна утаскивать за собой всю
пачку.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from dishka import AsyncContainer

from gamer_shop.application.dto import OutboxCommandDTO
from gamer_shop.application.enums import OutboxCommandKind
from gamer_shop.application.interactors import (
    DeliverOrderItemInteractor,
    RefundItemInteractor,
    SettleOrderInteractor,
)

Handler = Callable[[AsyncContainer, dict[str, Any]], Awaitable[None]]


async def _deliver_item(scope: AsyncContainer, payload: dict[str, Any]) -> None:
    interactor = await scope.get(DeliverOrderItemInteractor)
    await interactor.execute(payload['order_item_id'])


async def _refund_item(scope: AsyncContainer, payload: dict[str, Any]) -> None:
    interactor = await scope.get(RefundItemInteractor)
    await interactor.execute(
        payload['order_item_id'], payload.get('reason', 'delivery_failed')
    )


async def _settle_order(scope: AsyncContainer, payload: dict[str, Any]) -> None:
    interactor = await scope.get(SettleOrderInteractor)
    await interactor.execute(payload['order_id'])


_HANDLERS: dict[str, Handler] = {
    OutboxCommandKind.DELIVER_ITEM.value: _deliver_item,
    OutboxCommandKind.REFUND_ITEM.value: _refund_item,
    OutboxCommandKind.SETTLE_ORDER.value: _settle_order,
}


class DishkaCommandDispatcher:
    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    async def dispatch(self, command: OutboxCommandDTO) -> None:
        handler = _HANDLERS.get(command.kind)
        if handler is None:
            # Незнакомый вид — не молча пропускаем: релей отправит команду
            # в повтор, а после исчерпания попыток она всплывёт в сверке.
            raise LookupError(f'нет обработчика для команды {command.kind}')

        async with self._container() as scope:
            await handler(scope, command.payload)
