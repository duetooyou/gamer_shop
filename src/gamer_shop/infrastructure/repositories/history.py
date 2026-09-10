"""Чтение истории: состояние на момент и итоги за период.

Отдельного хранилища для «прошлого» нет и не нужно. Журнал состояний и
проводки денег только дополняются, поэтому срез на момент — это фильтр по
времени, а не вторая копия данных, которая рано или поздно разъедется
с первой.
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import (
    ItemSnapshotDTO,
    MoneySnapshotDTO,
    OrderEventDTO,
    PeriodTotalsDTO,
)

_EVENTS_SQL = text(
    """
    SELECT id, order_id, order_item_id, kind, from_status, to_status, payload, occurred_at
      FROM order_events
     WHERE order_id = :order_id AND occurred_at <= :as_of
     ORDER BY occurred_at, id
    """
)

# Статус — последнее событие о заказе на момент; сумма и валюта — из события
# о его создании. Оба подзапроса идут по одному индексу (order_id, occurred_at).
_ORDER_AT_SQL = text(
    """
    SELECT
      (SELECT e.to_status
         FROM order_events e
        WHERE e.order_id = :order_id AND e.occurred_at <= :as_of
          AND e.kind IN ('order_created', 'order_status_changed')
        ORDER BY e.occurred_at DESC, e.id DESC
        LIMIT 1) AS status,
      (SELECT e.payload
         FROM order_events e
        WHERE e.order_id = :order_id AND e.occurred_at <= :as_of
          AND e.kind = 'order_created'
        LIMIT 1) AS created
    """
)

# По каждой позиции: последний статус на момент, её описание из события
# создания и был ли к тому времени выдан код.
_ITEMS_AT_SQL = text(
    """
    SELECT e.order_item_id,
           (array_agg(e.to_status ORDER BY e.occurred_at DESC, e.id DESC)
              FILTER (WHERE e.kind IN ('item_created', 'item_status_changed')))[1]
               AS status,
           (array_agg(e.payload ORDER BY e.occurred_at, e.id)
              FILTER (WHERE e.kind = 'item_created'))[1] AS created,
           count(*) FILTER (WHERE e.kind = 'code_issued') > 0 AS code_issued
      FROM order_events e
     WHERE e.order_id = :order_id
       AND e.occurred_at <= :as_of
       AND e.order_item_id IS NOT NULL
     GROUP BY e.order_item_id
    """
)

_COUNTS_SQL = text(
    """
    SELECT
      count(*) FILTER (WHERE kind = 'order_created') AS orders_created,
      count(*) FILTER (WHERE kind = 'order_status_changed'
                         AND to_status IN ('delivered', 'partially_delivered',
                                           'refunded', 'payment_failed'))
          AS orders_settled,
      count(*) FILTER (WHERE kind = 'item_status_changed' AND to_status = 'delivered')
          AS items_delivered,
      count(*) FILTER (WHERE kind = 'item_status_changed' AND to_status = 'refunded')
          AS items_refunded
      FROM order_events
     WHERE occurred_at > :period_from AND occurred_at <= :period_to
    """
)


class SqlAlchemyOrderHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def events(self, order_id: str, as_of: datetime) -> list[OrderEventDTO]:
        rows = await self._session.execute(
            _EVENTS_SQL, {'order_id': order_id, 'as_of': as_of}
        )
        return [
            OrderEventDTO(
                id=row.id,
                order_id=row.order_id,
                order_item_id=row.order_item_id,
                kind=row.kind,
                from_status=row.from_status,
                to_status=row.to_status,
                payload=row.payload or {},
                occurred_at=row.occurred_at,
            )
            for row in rows
        ]

    async def order_state_at(
        self, order_id: str, as_of: datetime
    ) -> tuple[str, int, str] | None:
        row = (
            await self._session.execute(
                _ORDER_AT_SQL, {'order_id': order_id, 'as_of': as_of}
            )
        ).one()
        if row.status is None:
            # Заказа на тот момент ещё не было.
            return None
        created = row.created or {}
        return row.status, int(created.get('total_amount', 0)), created.get('currency', '')

    async def items_at(self, order_id: str, as_of: datetime) -> list[ItemSnapshotDTO]:
        rows = (
            await self._session.execute(
                _ITEMS_AT_SQL, {'order_id': order_id, 'as_of': as_of}
            )
        ).all()
        items = [
            ItemSnapshotDTO(
                order_item_id=row.order_item_id,
                position=int((row.created or {}).get('position', 0)),
                sku=(row.created or {}).get('sku', ''),
                price=int((row.created or {}).get('price', 0)),
                currency=(row.created or {}).get('currency', ''),
                supplier=(row.created or {}).get('supplier', ''),
                status=row.status,
                code_issued=bool(row.code_issued),
            )
            for row in rows
            if row.status is not None
        ]
        return sorted(items, key=lambda i: i.position)

    async def counts_between(
        self, period_from: datetime, period_to: datetime
    ) -> tuple[int, int, int, int]:
        row = (
            await self._session.execute(
                _COUNTS_SQL, {'period_from': period_from, 'period_to': period_to}
            )
        ).one()
        return (
            int(row.orders_created),
            int(row.orders_settled),
            int(row.items_delivered),
            int(row.items_refunded),
        )


# Обязательство перед покупателем: кредит минус дебет по customer_liability.
# Оно и есть «оплачено, но ещё не разрешилось» — ни выдачей, ни возвратом.
_LIABILITY = (
    "CASE WHEN account = 'customer_liability' "
    "     THEN CASE WHEN direction = 'credit' THEN amount ELSE -amount END "
    "     ELSE 0 END"
)

_MONEY_AT_SQL = text(
    """
    SELECT
      COALESCE(sum(amount) FILTER (
          WHERE account = 'cash_in' AND direction = 'debit'), 0) AS paid,
      COALESCE(sum(amount) FILTER (
          WHERE account = 'revenue' AND direction = 'credit'), 0) AS delivered,
      COALESCE(sum(amount) FILTER (
          WHERE account = 'refund_payable' AND direction = 'credit'), 0) AS refunded
      FROM ledger_entries
     WHERE order_id = :order_id AND created_at <= :as_of
    """
)

# Одним проходом: обороты за период и обязательство на его границах.
_TOTALS_SQL = text(
    f"""
    SELECT
      COALESCE(sum(amount) FILTER (
          WHERE account = 'cash_in' AND direction = 'debit'
            AND created_at > :period_from AND created_at <= :period_to), 0) AS paid,
      COALESCE(sum(amount) FILTER (
          WHERE account = 'revenue' AND direction = 'credit'
            AND created_at > :period_from AND created_at <= :period_to), 0) AS delivered,
      COALESCE(sum(amount) FILTER (
          WHERE account = 'refund_payable' AND direction = 'credit'
            AND created_at > :period_from AND created_at <= :period_to), 0) AS refunded,
      COALESCE(sum({_LIABILITY}) FILTER (WHERE created_at <= :period_from), 0)
          AS opening,
      COALESCE(sum({_LIABILITY}) FILTER (WHERE created_at <= :period_to), 0)
          AS closing
      FROM ledger_entries
    """
)


class SqlAlchemyLedgerHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def money_at(self, order_id: str, as_of: datetime) -> MoneySnapshotDTO:
        row = (
            await self._session.execute(
                _MONEY_AT_SQL, {'order_id': order_id, 'as_of': as_of}
            )
        ).one()
        return MoneySnapshotDTO(
            paid=int(row.paid), delivered=int(row.delivered), refunded=int(row.refunded)
        )

    async def totals_between(
        self, period_from: datetime, period_to: datetime
    ) -> PeriodTotalsDTO:
        row = (
            await self._session.execute(
                _TOTALS_SQL, {'period_from': period_from, 'period_to': period_to}
            )
        ).one()
        return PeriodTotalsDTO(
            period_from=period_from,
            period_to=period_to,
            paid=int(row.paid),
            delivered=int(row.delivered),
            refunded=int(row.refunded),
            opening_outstanding=int(row.opening),
            closing_outstanding=int(row.closing),
        )
