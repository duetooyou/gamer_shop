from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import OrderProblemDTO
from gamer_shop.application.enums import OrderStatus


class SqlAlchemyReconciliationRepository:
    """Запросы сверки.

    Явный SQL: отчётность по нескольким таблицам, читаемость важнее ORM.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def paid_not_delivered(
        self, older_than_seconds: int, limit: int
    ) -> list[OrderProblemDTO]:
        """Деньги приняли, кода у покупателя нет."""
        rows = await self._session.execute(
            text(
                """
                SELECT o.id, o.status, o.sku, o.price,
                       EXTRACT(epoch FROM now() - o.paid_at)::int AS age
                FROM orders o
                LEFT JOIN deliveries d ON d.order_id = o.id
                WHERE o.paid_at IS NOT NULL
                  AND d.order_id IS NULL
                  AND o.paid_at < now() - make_interval(secs => :age)
                ORDER BY o.paid_at
                LIMIT :limit
                """
            ),
            {'age': older_than_seconds, 'limit': limit},
        )
        return [
            OrderProblemDTO(
                order_id=r.id, status=r.status, sku=r.sku, amount=r.price, age_seconds=r.age
            )
            for r in rows
        ]

    async def delivered_not_paid(self, limit: int) -> list[OrderProblemDTO]:
        """В норме пусто. Непустой список — дефект."""
        rows = await self._session.execute(
            text(
                """
                SELECT o.id, o.status, o.sku, o.price,
                       EXTRACT(epoch FROM now() - d.delivered_at)::int AS age,
                       d.code
                FROM deliveries d
                JOIN orders o ON o.id = d.order_id
                WHERE o.paid_at IS NULL
                ORDER BY d.delivered_at
                LIMIT :limit
                """
            ),
            {'limit': limit},
        )
        return [
            OrderProblemDTO(
                order_id=r.id,
                status=r.status,
                sku=r.sku,
                amount=r.price,
                age_seconds=r.age,
                detail=f'code={r.code}',
            )
            for r in rows
        ]

    async def stuck_delivering(
        self, older_than_seconds: int, limit: int
    ) -> list[OrderProblemDTO]:
        rows = await self._session.execute(
            text(
                """
                SELECT o.id, o.status, o.sku, o.price,
                       EXTRACT(epoch FROM now() - o.updated_at)::int AS age,
                       string_agg(sr.supplier || '=' || sr.state, ',') AS suppliers
                FROM orders o
                LEFT JOIN supplier_requests sr ON sr.order_id = o.id
                WHERE o.status = :status
                  AND o.updated_at < now() - make_interval(secs => :age)
                GROUP BY o.id, o.status, o.sku, o.price, o.updated_at
                ORDER BY o.updated_at
                LIMIT :limit
                """
            ),
            {'status': OrderStatus.DELIVERING.value, 'age': older_than_seconds, 'limit': limit},
        )
        return [
            OrderProblemDTO(
                order_id=r.id,
                status=r.status,
                sku=r.sku,
                amount=r.price,
                age_seconds=r.age,
                detail=r.suppliers,
            )
            for r in rows
        ]

    async def stock_drift(self) -> list[str]:
        """reserved_count должен равняться числу заказов в delivering по SKU.

        Расхождение означает утёкший резерв — остаток съеден без выдачи.
        """
        rows = await self._session.execute(
            text(
                """
                SELECT s.sku
                FROM product_stock s
                LEFT JOIN (
                    SELECT sku, count(*) AS cnt
                    FROM orders WHERE status = :status GROUP BY sku
                ) o ON o.sku = s.sku
                WHERE s.reserved_count <> COALESCE(o.cnt, 0)
                """
            ),
            {'status': OrderStatus.DELIVERING.value},
        )
        return [r.sku for r in rows]
