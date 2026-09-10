from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import DiscrepancyDTO, NewDiscrepancyDTO
from gamer_shop.application.enums import DiscrepancyState
from gamer_shop.infrastructure.models import SupplierDiscrepancyORM


class SqlAlchemyDiscrepancyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, discrepancy: NewDiscrepancyDTO) -> bool:
        resolved = discrepancy.resolution is not None
        stmt = (
            pg_insert(SupplierDiscrepancyORM)
            .values(
                kind=discrepancy.kind.value,
                supplier=discrepancy.supplier.value,
                request_id=discrepancy.request_id,
                order_item_id=discrepancy.order_item_id,
                code=discrepancy.code,
                detail=discrepancy.detail,
                state=(
                    DiscrepancyState.RESOLVED.value
                    if resolved
                    else DiscrepancyState.OPEN.value
                ),
                resolution=discrepancy.resolution,
                resolved_at=datetime.now(UTC) if resolved else None,
            )
            # Повторный проход по тому же расхождению не плодит строк.
            .on_conflict_do_nothing(index_elements=['kind', 'request_id', 'code'])
            .returning(SupplierDiscrepancyORM.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def resolve(self, discrepancy_id: int, resolution: str) -> None:
        await self._session.execute(
            text(
                "UPDATE supplier_discrepancies SET state = 'resolved', "
                'resolution = :resolution, resolved_at = now() '
                "WHERE id = :id AND state = 'open'"
            ),
            {'id': discrepancy_id, 'resolution': resolution[:255]},
        )

    async def open_ones(self, limit: int) -> list[DiscrepancyDTO]:
        stmt = (
            select(SupplierDiscrepancyORM)
            .where(SupplierDiscrepancyORM.state == DiscrepancyState.OPEN.value)
            .order_by(SupplierDiscrepancyORM.created_at)
            .limit(limit)
        )
        return [_to_dto(o) for o in (await self._session.execute(stmt)).scalars().all()]

    async def all_for_request(self, request_id: str) -> list[DiscrepancyDTO]:
        stmt = (
            select(SupplierDiscrepancyORM)
            .where(SupplierDiscrepancyORM.request_id == request_id)
            .order_by(SupplierDiscrepancyORM.created_at)
        )
        return [_to_dto(o) for o in (await self._session.execute(stmt)).scalars().all()]

    async def counts_by_kind(self) -> dict[str, int]:
        stmt = select(
            SupplierDiscrepancyORM.kind, func.count()
        ).group_by(SupplierDiscrepancyORM.kind)
        return {kind: int(count) for kind, count in (await self._session.execute(stmt)).all()}


def _to_dto(orm: SupplierDiscrepancyORM) -> DiscrepancyDTO:
    return DiscrepancyDTO(
        id=orm.id,
        kind=orm.kind,
        supplier=orm.supplier,
        request_id=orm.request_id,
        order_item_id=orm.order_item_id,
        code=orm.code,
        detail=orm.detail,
        state=orm.state,
        resolution=orm.resolution,
        created_at=orm.created_at,
        resolved_at=orm.resolved_at,
    )
