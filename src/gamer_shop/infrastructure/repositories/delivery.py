from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import extract, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import (
    DeliveryDTO,
    OrderProblemDTO,
    SupplierOutcome,
    SupplierOutcomeKind,
    SupplierRequestSnapshot,
)
from gamer_shop.application.enums import SupplierName, SupplierRequestState
from gamer_shop.infrastructure.models import (
    DeliveryORM,
    OrderItemORM,
    SupplierRequestORM,
)

_KIND_TO_STATE = {
    SupplierOutcomeKind.OK: SupplierRequestState.OK,
    SupplierOutcomeKind.REFUSED: SupplierRequestState.REFUSED,
    SupplierOutcomeKind.UNKNOWN: SupplierRequestState.UNKNOWN,
}
_STATE_TO_KIND = {v: k for k, v in _KIND_TO_STATE.items()}


def _to_dto(orm: DeliveryORM) -> DeliveryDTO:
    return DeliveryDTO(
        order_item_id=orm.order_item_id,
        code=orm.code,
        supplier=orm.supplier,
        request_id=orm.request_id,
        delivered_at=orm.delivered_at,
    )


class SqlAlchemyDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_if_absent(
        self, order_item_id: str, code: str, supplier: str, request_id: str
    ) -> bool:
        """Без указания ограничения — гасит оба конфликта: по позиции и по коду."""
        stmt = (
            pg_insert(DeliveryORM)
            .values(
                id=uuid4(),
                order_item_id=order_item_id,
                code=code,
                supplier=supplier,
                request_id=request_id,
                delivered_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
            .returning(DeliveryORM.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def code_taken_by_other_item(self, code: str, order_item_id: str) -> bool:
        stmt = select(DeliveryORM.order_item_id).where(DeliveryORM.code == code)
        owner = (await self._session.execute(stmt)).scalar_one_or_none()
        return owner is not None and owner != order_item_id

    async def get_by_item(self, order_item_id: str) -> DeliveryDTO | None:
        stmt = select(DeliveryORM).where(DeliveryORM.order_item_id == order_item_id)
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_dto(orm) if orm is not None else None

    async def list_by_order(self, order_id: str) -> dict[str, DeliveryDTO]:
        """Коды заказа, разложенные по позициям."""
        stmt = (
            select(DeliveryORM)
            .join(OrderItemORM, OrderItemORM.id == DeliveryORM.order_item_id)
            .where(OrderItemORM.order_id == order_id)
        )
        return {
            orm.order_item_id: _to_dto(orm)
            for orm in (await self._session.execute(stmt)).scalars().all()
        }


class SqlAlchemySupplierRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self, order_item_id: str, supplier: SupplierName, sku: str, request_id: str
    ) -> SupplierRequestSnapshot:
        await self._session.execute(
            pg_insert(SupplierRequestORM)
            .values(
                request_id=request_id,
                order_item_id=order_item_id,
                supplier=supplier.value,
                sku=sku,
                state=SupplierRequestState.PENDING.value,
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=['request_id'])
        )
        orm = await self._session.get(SupplierRequestORM, request_id)
        return SupplierRequestSnapshot(
            state=SupplierRequestState(orm.state),
            request_id=orm.request_id,
            supplier=SupplierName(orm.supplier),
            code=orm.code,
            reason=orm.last_error,
            attempts=orm.attempts,
        )

    async def save_outcome(self, outcome: SupplierOutcome) -> None:
        await self._session.execute(
            update(SupplierRequestORM)
            .where(SupplierRequestORM.request_id == outcome.request_id)
            .values(
                state=_KIND_TO_STATE[outcome.kind].value,
                code=outcome.code,
                last_error=outcome.reason,
                attempts=SupplierRequestORM.attempts + outcome.attempts,
                updated_at=datetime.now(UTC),
            )
        )

    async def state_of(
        self, order_item_id: str, supplier: SupplierName
    ) -> SupplierRequestState | None:
        stmt = select(SupplierRequestORM.state).where(
            SupplierRequestORM.order_item_id == order_item_id,
            SupplierRequestORM.supplier == supplier.value,
        )
        raw = (await self._session.execute(stmt)).scalar_one_or_none()
        return SupplierRequestState(raw) if raw is not None else None

    async def unresolved(self, limit: int) -> list[OrderProblemDTO]:
        stmt = (
            select(
                SupplierRequestORM.order_item_id,
                SupplierRequestORM.request_id,
                SupplierRequestORM.supplier,
                SupplierRequestORM.sku,
                SupplierRequestORM.attempts,
                extract('epoch', func.now() - SupplierRequestORM.updated_at).label('age'),
            )
            .where(SupplierRequestORM.state == SupplierRequestState.UNKNOWN.value)
            .order_by(SupplierRequestORM.updated_at)
            .limit(limit)
        )
        return [
            OrderProblemDTO(
                order_id=r.order_item_id,
                status=f'supplier_{r.supplier}:unknown',
                sku=r.sku,
                amount=0,
                age_seconds=int(r.age or 0),
                detail=f'request_id={r.request_id}, attempts={r.attempts}',
            )
            for r in (await self._session.execute(stmt)).all()
        ]
