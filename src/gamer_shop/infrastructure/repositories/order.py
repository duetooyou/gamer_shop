from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import OrderDTO
from gamer_shop.application.enums import OrderStatus
from gamer_shop.infrastructure.models import OrderORM


class SqlAlchemyOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, sku: str, price: int, currency: str) -> OrderDTO:
        orm = OrderORM(sku=sku, price=price, currency=currency)
        self._session.add(orm)
        await self._session.flush()
        # id и статус проставляет БД — забираем обратно.
        await self._session.refresh(orm)
        return self._to_dto(orm)

    async def get_by_id(self, order_id: str) -> OrderDTO | None:
        orm = await self._session.get(OrderORM, order_id)
        return self._to_dto(orm) if orm is not None else None

    async def lock_by_id(self, order_id: str) -> OrderDTO | None:
        stmt = select(OrderORM).where(OrderORM.id == order_id).with_for_update()
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_dto(orm) if orm is not None else None

    async def try_reclaim_stale_delivering(
        self, order_id: str, stale_after_seconds: int
    ) -> bool:
        result = await self._session.execute(
            text(
                "UPDATE orders SET updated_at = now() "
                "WHERE id = :id AND status = :status "
                "  AND updated_at < now() - make_interval(secs => :stale)"
            ),
            {
                'id': order_id,
                'status': OrderStatus.DELIVERING.value,
                'stale': stale_after_seconds,
            },
        )
        return result.rowcount == 1

    async def try_transition(
        self,
        order_id: str,
        target: OrderStatus,
        allowed_sources: frozenset[OrderStatus],
        failure_reason: str | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        values: dict = {'status': target.value, 'updated_at': now, 'failure_reason': failure_reason}
        if target is OrderStatus.PAID:
            values['paid_at'] = now
        if target is OrderStatus.DELIVERED:
            values['delivered_at'] = now

        stmt = (
            update(OrderORM)
            .where(
                OrderORM.id == order_id,
                OrderORM.status.in_([s.value for s in allowed_sources]),
            )
            .values(**values)
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def find_stale(
        self, statuses: frozenset[OrderStatus], older_than_seconds: int, limit: int
    ) -> list[str]:
        stmt = (
            select(OrderORM.id)
            .where(
                OrderORM.status.in_([s.value for s in statuses]),
                OrderORM.updated_at
                < func.now() - text(f"interval '{int(older_than_seconds)} seconds'"),
            )
            .order_by(OrderORM.updated_at)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    @staticmethod
    def _to_dto(orm: OrderORM) -> OrderDTO:
        return OrderDTO(
            id=orm.id,
            sku=orm.sku,
            price=orm.price,
            currency=orm.currency,
            status=OrderStatus(orm.status),
            created_at=orm.created_at,
            updated_at=orm.updated_at,
            paid_at=orm.paid_at,
            delivered_at=orm.delivered_at,
            failure_reason=orm.failure_reason,
        )
