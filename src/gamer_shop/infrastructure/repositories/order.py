from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import (
    ItemStatusCountsDTO,
    NewOrderLine,
    OrderDTO,
    OrderItemDTO,
)
from gamer_shop.application.enums import OrderItemStatus, OrderStatus, SupplierName
from gamer_shop.application.policies import build_item_id
from gamer_shop.infrastructure.models import OrderItemORM, OrderORM


class SqlAlchemyOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, lines: list[NewOrderLine]) -> tuple[OrderDTO, list[OrderItemDTO]]:
        order = OrderORM(
            total_amount=sum(line.price for line in lines),
            currency=lines[0].currency,
        )
        self._session.add(order)
        # id проставляет БД — забираем до того, как выводить из него позиции.
        await self._session.flush()
        await self._session.refresh(order)

        items = [
            OrderItemORM(
                id=build_item_id(order.id, position),
                order_id=order.id,
                position=position,
                sku=line.sku,
                price=line.price,
                currency=line.currency,
                supplier=line.supplier.value,
                status=OrderItemStatus.PENDING.value,
            )
            for position, line in enumerate(lines, start=1)
        ]
        self._session.add_all(items)
        await self._session.flush()
        for item in items:
            await self._session.refresh(item)

        return self._to_dto(order), [_item_to_dto(i) for i in items]

    async def get_by_id(self, order_id: str) -> OrderDTO | None:
        orm = await self._session.get(OrderORM, order_id)
        return self._to_dto(orm) if orm is not None else None

    async def lock_by_id(self, order_id: str) -> OrderDTO | None:
        stmt = select(OrderORM).where(OrderORM.id == order_id).with_for_update()
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_dto(orm) if orm is not None else None

    async def try_transition(
        self,
        order_id: str,
        target: OrderStatus,
        allowed_sources: frozenset[OrderStatus],
        failure_reason: str | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        values: dict = {
            'status': target.value,
            'updated_at': now,
            'failure_reason': failure_reason,
        }
        if target is OrderStatus.PAID:
            values['paid_at'] = now
        if target in (
            OrderStatus.DELIVERED,
            OrderStatus.PARTIALLY_DELIVERED,
            OrderStatus.REFUNDED,
        ):
            values['settled_at'] = now

        stmt = (
            update(OrderORM)
            .where(
                OrderORM.id == order_id,
                OrderORM.status.in_([s.value for s in allowed_sources]),
            )
            .values(**values)
        )
        return (await self._session.execute(stmt)).rowcount == 1

    async def find_unsettled(self, older_than_seconds: int, limit: int) -> list[str]:
        """Все позиции терминальны, а заказ не закрыт."""
        rows = await self._session.execute(
            text(
                """
                SELECT o.id
                  FROM orders o
                  JOIN order_items i ON i.order_id = o.id
                 WHERE o.status IN ('paid', 'delivering')
                   AND o.updated_at < now() - make_interval(secs => :age)
                 GROUP BY o.id
                HAVING count(*) FILTER (
                           WHERE i.status NOT IN ('delivered', 'refunded')
                       ) = 0
                 LIMIT :limit
                """
            ),
            {'age': older_than_seconds, 'limit': limit},
        )
        return [r.id for r in rows]

    @staticmethod
    def _to_dto(orm: OrderORM) -> OrderDTO:
        return OrderDTO(
            id=orm.id,
            total_amount=orm.total_amount,
            currency=orm.currency,
            status=OrderStatus(orm.status),
            created_at=orm.created_at,
            updated_at=orm.updated_at,
            paid_at=orm.paid_at,
            settled_at=orm.settled_at,
            failure_reason=orm.failure_reason,
        )


class SqlAlchemyOrderItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, item_id: str) -> OrderItemDTO | None:
        orm = await self._session.get(OrderItemORM, item_id)
        return _item_to_dto(orm) if orm is not None else None

    async def list_by_order(self, order_id: str) -> list[OrderItemDTO]:
        stmt = (
            select(OrderItemORM)
            .where(OrderItemORM.order_id == order_id)
            .order_by(OrderItemORM.position)
        )
        return [_item_to_dto(o) for o in (await self._session.execute(stmt)).scalars().all()]

    async def mark_paid(self, order_id: str) -> list[OrderItemDTO]:
        await self._session.execute(
            update(OrderItemORM)
            .where(
                OrderItemORM.order_id == order_id,
                OrderItemORM.status == OrderItemStatus.PENDING.value,
            )
            .values(status=OrderItemStatus.PAID.value, updated_at=datetime.now(UTC))
        )
        return await self.list_by_order(order_id)

    async def try_transition(
        self,
        item_id: str,
        target: OrderItemStatus,
        allowed_sources: frozenset[OrderItemStatus],
        failure_reason: str | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        values: dict = {
            'status': target.value,
            'updated_at': now,
            'failure_reason': failure_reason,
        }
        if target is OrderItemStatus.DELIVERING:
            values['delivery_attempts'] = OrderItemORM.delivery_attempts + 1
        if target is OrderItemStatus.DELIVERED:
            values['delivered_at'] = now
        if target is OrderItemStatus.REFUNDED:
            values['refunded_at'] = now

        stmt = (
            update(OrderItemORM)
            .where(
                OrderItemORM.id == item_id,
                OrderItemORM.status.in_([s.value for s in allowed_sources]),
            )
            .values(**values)
        )
        return (await self._session.execute(stmt)).rowcount == 1

    async def try_reclaim_stale_delivering(
        self, item_id: str, stale_after_seconds: int
    ) -> bool:
        result = await self._session.execute(
            text(
                'UPDATE order_items '
                '   SET updated_at = now(), delivery_attempts = delivery_attempts + 1 '
                ' WHERE id = :id AND status = :status '
                '   AND updated_at < now() - make_interval(secs => :stale)'
            ),
            {
                'id': item_id,
                'status': OrderItemStatus.DELIVERING.value,
                'stale': stale_after_seconds,
            },
        )
        return result.rowcount == 1

    async def status_counts(self, order_id: str) -> ItemStatusCountsDTO:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT count(*) AS total,
                           count(*) FILTER (WHERE status = 'delivered') AS delivered,
                           count(*) FILTER (WHERE status = 'refunded')  AS refunded,
                           COALESCE(sum(price) FILTER (WHERE status = 'delivered'), 0)
                               AS delivered_amount,
                           COALESCE(sum(price) FILTER (WHERE status = 'refunded'), 0)
                               AS refunded_amount
                      FROM order_items
                     WHERE order_id = :order_id
                    """
                ),
                {'order_id': order_id},
            )
        ).one()
        return ItemStatusCountsDTO(
            total=int(row.total),
            delivered=int(row.delivered),
            refunded=int(row.refunded),
            delivered_amount=int(row.delivered_amount),
            refunded_amount=int(row.refunded_amount),
        )

    async def find_stale(
        self,
        statuses: frozenset[OrderItemStatus],
        older_than_seconds: int,
        limit: int,
    ) -> list[str]:
        stmt = (
            select(OrderItemORM.id)
            .where(
                OrderItemORM.status.in_([s.value for s in statuses]),
                OrderItemORM.updated_at
                < func.now() - text(f"interval '{int(older_than_seconds)} seconds'"),
            )
            .order_by(OrderItemORM.updated_at)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())


def _item_to_dto(orm: OrderItemORM) -> OrderItemDTO:
    return OrderItemDTO(
        id=orm.id,
        order_id=orm.order_id,
        position=orm.position,
        sku=orm.sku,
        price=orm.price,
        currency=orm.currency,
        supplier=SupplierName(orm.supplier),
        status=OrderItemStatus(orm.status),
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        delivery_attempts=orm.delivery_attempts,
        delivered_at=orm.delivered_at,
        refunded_at=orm.refunded_at,
        failure_reason=orm.failure_reason,
    )
