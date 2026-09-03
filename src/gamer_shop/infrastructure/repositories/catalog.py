from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import ProductDTO, StorefrontItemDTO, StorefrontPageDTO
from gamer_shop.infrastructure.models import ProductORM, ProductStockORM


class SqlAlchemyProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_by_sku(self, sku: str) -> ProductDTO | None:
        stmt = select(ProductORM).where(ProductORM.sku == sku, ProductORM.is_active.is_(True))
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_dto(orm) if orm is not None else None

    async def storefront_page(
        self, product_type: str | None, cursor: str | None, limit: int
    ) -> StorefrontPageDTO:
        """Витрина с остатками.

        Страница отбирается из products по покрывающему индексу, и только
        потом к готовым строкам подтягивается остаток по PK: плоский join
        на тысячах SKU уводит планировщик в merge join. Лишняя строка сверх
        limit — чтобы узнать про следующую страницу без COUNT(*).
        """
        page = (
            select(
                ProductORM.sku,
                ProductORM.name,
                ProductORM.type,
                ProductORM.price,
                ProductORM.currency,
            )
            .where(ProductORM.is_active.is_(True))
            .order_by(ProductORM.sku)
            .limit(limit + 1)
        )
        if product_type is not None:
            page = page.where(ProductORM.type == product_type)
        if cursor is not None:
            page = page.where(ProductORM.sku > cursor)
        page = page.subquery('page')

        stmt = (
            select(
                page.c.sku,
                page.c.name,
                page.c.type,
                page.c.price,
                page.c.currency,
                ProductStockORM.available_count,
            )
            .join(ProductStockORM, ProductStockORM.sku == page.c.sku)
            .order_by(page.c.sku)
        )

        rows = (await self._session.execute(stmt)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            StorefrontItemDTO(
                sku=r.sku,
                name=r.name,
                type=r.type,
                price=r.price,
                currency=r.currency,
                available_count=r.available_count,
            )
            for r in rows
        ]
        return StorefrontPageDTO(
            items=items, next_cursor=items[-1].sku if has_more and items else None
        )

    @staticmethod
    def _to_dto(orm: ProductORM) -> ProductDTO:
        return ProductDTO(
            sku=orm.sku,
            name=orm.name,
            type=orm.type,
            price=orm.price,
            currency=orm.currency,
            image=orm.image,
            is_active=orm.is_active,
        )


class SqlAlchemyStockRepository:
    """Остаток по SKU.

    Все операции — UPDATE с условием в WHERE: при параллельных выдачах
    остаток не уйдёт в минус и блокировки не нужны.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(self, sku: str) -> bool:
        result = await self._session.execute(
            text(
                'UPDATE product_stock '
                'SET available_count = available_count - 1, '
                '    reserved_count = reserved_count + 1 '
                'WHERE sku = :sku AND available_count > 0 '
                'RETURNING available_count'
            ),
            {'sku': sku},
        )
        return result.scalar_one_or_none() is not None

    async def release(self, sku: str) -> None:
        await self._session.execute(
            text(
                'UPDATE product_stock '
                'SET available_count = available_count + 1, '
                '    reserved_count = reserved_count - 1 '
                'WHERE sku = :sku AND reserved_count > 0'
            ),
            {'sku': sku},
        )

    async def commit_reserved(self, sku: str) -> None:
        await self._session.execute(
            text(
                'UPDATE product_stock SET reserved_count = reserved_count - 1 '
                'WHERE sku = :sku AND reserved_count > 0'
            ),
            {'sku': sku},
        )

    async def refill(self, sku: str, count: int) -> int:
        stmt = (
            pg_insert(ProductStockORM)
            .values(sku=sku, available_count=count, reserved_count=0)
            .on_conflict_do_update(
                index_elements=['sku'],
                set_={'available_count': ProductStockORM.available_count + count},
            )
            .returning(ProductStockORM.available_count)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def available(self, sku: str) -> int:
        stmt = select(ProductStockORM.available_count).where(ProductStockORM.sku == sku)
        return (await self._session.execute(stmt)).scalar_one_or_none() or 0
