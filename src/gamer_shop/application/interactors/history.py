"""Восстановление картины на прошлый момент.

Состояние хранится перезаписью, поэтому «как было» из таблиц заказов не
достать. Достаётся из двух журналов, которые только дополняются: журнала
состояний заказа и журнала денежных движений. Никакого третьего хранилища
для истории нет — иначе оно рано или поздно разъедется с первыми двумя.
"""

from datetime import UTC, datetime

from gamer_shop.application.dto import (
    OrderSnapshotDTO,
    PeriodTotalsDTO,
)
from gamer_shop.application.interfaces import Clock
from gamer_shop.application.repositories import (
    LedgerHistoryRepository,
    OrderHistoryRepository,
)


class OrderStateAtInteractor:
    """Состояние заказа и денег на указанный момент."""

    def __init__(
        self,
        history: OrderHistoryRepository,
        ledger: LedgerHistoryRepository,
        clock: Clock,
    ) -> None:
        self._history = history
        self._ledger = ledger
        self._clock = clock

    async def execute(
        self, order_id: str, as_of: datetime | None = None, with_events: bool = False
    ) -> OrderSnapshotDTO:
        moment = _aware(as_of) if as_of is not None else self._clock.now()

        state = await self._history.order_state_at(order_id, moment)
        if state is None:
            # Заказа на тот момент не существовало. Это не «не найден»:
            # сегодня он есть, а вчера его не было — законный ответ.
            return OrderSnapshotDTO(order_id=order_id, as_of=moment, exists=False)

        status, total_amount, currency = state
        return OrderSnapshotDTO(
            order_id=order_id,
            as_of=moment,
            exists=True,
            status=status,
            total_amount=total_amount,
            currency=currency,
            items=await self._history.items_at(order_id, moment),
            money=await self._ledger.money_at(order_id, moment),
            events=await self._history.events(order_id, moment) if with_events else [],
        )


class PeriodTotalsInteractor:
    """Итоги за период — из тех же журналов, что и состояние на момент.

    Сходимость проверяется тождеством двойной записи: пришло за период
    столько же, сколько за него выдано, возвращено и осталось висеть
    обязательством перед покупателем. Расхождение здесь означает, что
    разошлись данные, а не что отчёт посчитан иначе.
    """

    def __init__(
        self,
        history: OrderHistoryRepository,
        ledger: LedgerHistoryRepository,
        clock: Clock,
    ) -> None:
        self._history = history
        self._ledger = ledger
        self._clock = clock

    async def execute(
        self, period_from: datetime | None, period_to: datetime | None
    ) -> PeriodTotalsDTO:
        start = _aware(period_from) if period_from else datetime.fromtimestamp(0, UTC)
        end = _aware(period_to) if period_to else self._clock.now()

        totals = await self._ledger.totals_between(start, end)
        created, settled, delivered, refunded = await self._history.counts_between(
            start, end
        )
        return PeriodTotalsDTO(
            period_from=start,
            period_to=end,
            paid=totals.paid,
            delivered=totals.delivered,
            refunded=totals.refunded,
            opening_outstanding=totals.opening_outstanding,
            closing_outstanding=totals.closing_outstanding,
            orders_created=created,
            orders_settled=settled,
            items_delivered=delivered,
            items_refunded=refunded,
        )


def _aware(moment: datetime) -> datetime:
    """Момент без зоны считаем UTC: сравнивать его придётся с timestamptz."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
