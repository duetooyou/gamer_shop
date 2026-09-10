"""Прогресс разбора всплеска.

Три запроса вместо одного соединения: заказы, позиции и очередь живут в
разных таблицах и разной кардинальности, а join ради отчёта заставил бы
планировщик читать всё сразу.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import ProgressDTO, SupplierQueueDTO
from gamer_shop.application.repositories.rate_limit import SupplierRateLimitRepository

_ORDERS_SQL = text(
    """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status IN ('paid', 'delivering')) AS in_queue,
           count(*) FILTER (WHERE status IN ('delivered', 'partially_delivered',
                                             'refunded', 'payment_failed')) AS settled
      FROM orders
    """
)

_ITEMS_SQL = text(
    """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'delivered') AS delivered,
           count(*) FILTER (WHERE status = 'refunded')  AS refunded,
           count(*) FILTER (WHERE status IN ('paid', 'delivering',
                                             'out_of_stock', 'delivery_failed'))
               AS in_queue
      FROM order_items
    """
)

# Очередь обращений к поставщику: только команды выдачи, только незакрытые.
_QUEUE_SQL = text(
    """
    SELECT partition_key AS supplier,
           count(*) AS queued,
           count(*) FILTER (WHERE available_at <= clock_timestamp()) AS ready,
           EXTRACT(EPOCH FROM (clock_timestamp() - min(created_at))) AS oldest_wait
      FROM outbox
     WHERE state = 'pending' AND kind = 'deliver_item'
     GROUP BY partition_key
    """
)

_IN_FLIGHT_SQL = text(
    """
    SELECT supplier, count(*) AS in_flight
      FROM order_items
     WHERE status = 'delivering'
     GROUP BY supplier
    """
)

_DEAD_SQL = text("SELECT count(*) AS dead FROM outbox WHERE state = 'dead'")


class SqlAlchemyProgressRepository:
    def __init__(
        self, session: AsyncSession, limits: SupplierRateLimitRepository
    ) -> None:
        self._session = session
        self._limits = limits

    async def snapshot(self) -> ProgressDTO:
        orders = (await self._session.execute(_ORDERS_SQL)).one()
        items = (await self._session.execute(_ITEMS_SQL)).one()
        queue = (await self._session.execute(_QUEUE_SQL)).all()
        in_flight = {
            row.supplier: int(row.in_flight)
            for row in (await self._session.execute(_IN_FLIGHT_SQL)).all()
        }
        dead = (await self._session.execute(_DEAD_SQL)).scalar_one()

        by_supplier = {row.supplier: row for row in queue}
        suppliers = sorted(set(by_supplier) | set(in_flight))
        return ProgressDTO(
            orders_total=int(orders.total),
            orders_in_queue=int(orders.in_queue),
            orders_settled=int(orders.settled),
            items_total=int(items.total),
            items_in_queue=int(items.in_queue),
            items_delivered=int(items.delivered),
            items_refunded=int(items.refunded),
            queue=[
                SupplierQueueDTO(
                    supplier=supplier,
                    queued=int(by_supplier[supplier].queued) if supplier in by_supplier else 0,
                    ready=int(by_supplier[supplier].ready) if supplier in by_supplier else 0,
                    in_flight=in_flight.get(supplier, 0),
                    oldest_wait_seconds=(
                        round(float(by_supplier[supplier].oldest_wait), 3)
                        if supplier in by_supplier
                        and by_supplier[supplier].oldest_wait is not None
                        else None
                    ),
                )
                for supplier in suppliers
            ],
            rate_limits=await self._limits.state(),
            dead_commands=int(dead),
        )
