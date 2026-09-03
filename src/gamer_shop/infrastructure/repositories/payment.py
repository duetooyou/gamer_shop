from datetime import UTC, datetime

from sqlalchemy import extract, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import OrderProblemDTO, PaymentWebhookDTO
from gamer_shop.application.enums import PaymentStatus
from gamer_shop.infrastructure.models import PaymentEventORM


class SqlAlchemyPaymentEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_new(self, event: PaymentWebhookDTO) -> bool:
        """ON CONFLICT атомарен: из двух одинаковых event_id вставится один."""
        stmt = (
            pg_insert(PaymentEventORM)
            .values(
                event_id=event.event_id,
                order_id=event.order_id,
                status=event.status.value,
                amount=event.amount,
                currency=event.currency,
                occurred_at=event.created_at,
                applied=False,
                raw=event.raw,
            )
            .on_conflict_do_nothing(index_elements=['event_id'])
            .returning(PaymentEventORM.event_id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def mark_applied(self, event_id: str) -> None:
        await self._session.execute(
            update(PaymentEventORM)
            .where(PaymentEventORM.event_id == event_id)
            .values(applied=True, applied_at=datetime.now(UTC), reject_reason=None)
        )

    async def mark_rejected(self, event_id: str, reason: str) -> None:
        await self._session.execute(
            update(PaymentEventORM)
            .where(PaymentEventORM.event_id == event_id)
            .values(applied=False, reject_reason=reason)
        )

    async def list_unapplied(self, limit: int) -> list[PaymentWebhookDTO]:
        stmt = (
            select(PaymentEventORM)
            .where(PaymentEventORM.applied.is_(False))
            .order_by(PaymentEventORM.received_at)
            .limit(limit)
        )
        return [self._to_dto(o) for o in (await self._session.execute(stmt)).scalars()]

    async def unapplied_report(self, limit: int) -> list[OrderProblemDTO]:
        stmt = (
            select(
                PaymentEventORM.event_id,
                PaymentEventORM.order_id,
                PaymentEventORM.amount,
                PaymentEventORM.status,
                PaymentEventORM.reject_reason,
                extract('epoch', func.now() - PaymentEventORM.received_at).label('age'),
            )
            .where(PaymentEventORM.applied.is_(False))
            .order_by(PaymentEventORM.received_at)
            .limit(limit)
        )
        return [
            OrderProblemDTO(
                order_id=r.order_id,
                status=f'event:{r.status}',
                sku='',
                amount=r.amount,
                age_seconds=int(r.age or 0),
                detail=r.reject_reason or f'event_id={r.event_id}',
            )
            for r in (await self._session.execute(stmt)).all()
        ]

    @staticmethod
    def _to_dto(orm: PaymentEventORM) -> PaymentWebhookDTO:
        return PaymentWebhookDTO(
            event_id=orm.event_id,
            order_id=orm.order_id,
            status=PaymentStatus(orm.status),
            amount=orm.amount,
            currency=orm.currency,
            created_at=orm.occurred_at,
            raw=orm.raw,
        )
