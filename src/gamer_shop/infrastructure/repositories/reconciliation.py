from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import OrderProblemDTO
from gamer_shop.application.enums import OrderItemStatus


class SqlAlchemyReconciliationRepository:
    """Запросы сверки.

    Явный SQL: отчётность по нескольким таблицам, читаемость важнее ORM.
    Единица разбирательства — позиция: заказ может быть выдан наполовину,
    и «выдан/не выдан» про него уже не вопрос.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def paid_not_delivered(
        self, older_than_seconds: int, limit: int
    ) -> list[OrderProblemDTO]:
        """Деньги приняли, а позиция не закрыта ни кодом, ни возвратом."""
        rows = await self._session.execute(
            text(
                """
                SELECT i.id, i.status, i.sku, i.price,
                       EXTRACT(epoch FROM now() - o.paid_at)::int AS age
                  FROM order_items i
                  JOIN orders o ON o.id = i.order_id
                 WHERE o.paid_at IS NOT NULL
                   AND i.status NOT IN ('delivered', 'refunded')
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
                SELECT i.id, i.status, i.sku, i.price,
                       EXTRACT(epoch FROM now() - d.delivered_at)::int AS age,
                       d.code
                  FROM deliveries d
                  JOIN order_items i ON i.id = d.order_item_id
                  JOIN orders o ON o.id = i.order_id
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
                SELECT i.id, i.status, i.sku, i.price,
                       EXTRACT(epoch FROM now() - i.updated_at)::int AS age,
                       string_agg(sr.supplier || '=' || sr.state, ',') AS suppliers
                  FROM order_items i
                  LEFT JOIN supplier_requests sr ON sr.order_item_id = i.id
                 WHERE i.status = :status
                   AND i.updated_at < now() - make_interval(secs => :age)
                 GROUP BY i.id, i.status, i.sku, i.price, i.updated_at
                 ORDER BY i.updated_at
                 LIMIT :limit
                """
            ),
            {
                'status': OrderItemStatus.DELIVERING.value,
                'age': older_than_seconds,
                'limit': limit,
            },
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

    async def money_mismatch(self, limit: int) -> list[OrderProblemDTO]:
        """Заказы, где оплачено не равно выдано плюс возвращено.

        Главная проверка задания, и считается она по проводкам, а не по
        статусам: расхождение между тем и другим здесь и всплывёт.
        """
        rows = await self._session.execute(
            text(
                """
                SELECT o.id, o.status, o.total_amount,
                       EXTRACT(epoch FROM now() - o.updated_at)::int AS age,
                       COALESCE(sum(l.amount) FILTER (
                           WHERE l.account = 'cash_in' AND l.direction = 'debit'), 0) AS paid,
                       COALESCE(sum(l.amount) FILTER (
                           WHERE l.account = 'revenue' AND l.direction = 'credit'), 0)
                           AS delivered,
                       COALESCE(sum(l.amount) FILTER (
                           WHERE l.account = 'refund_payable' AND l.direction = 'credit'), 0)
                           AS refunded
                  FROM orders o
                  JOIN ledger_entries l ON l.order_id = o.id
                 WHERE o.status IN ('delivered', 'partially_delivered', 'refunded')
                 GROUP BY o.id, o.status, o.total_amount, o.updated_at
                HAVING COALESCE(sum(l.amount) FILTER (
                           WHERE l.account = 'cash_in' AND l.direction = 'debit'), 0)
                       <> COALESCE(sum(l.amount) FILTER (
                           WHERE l.account = 'revenue' AND l.direction = 'credit'), 0)
                        + COALESCE(sum(l.amount) FILTER (
                           WHERE l.account = 'refund_payable' AND l.direction = 'credit'), 0)
                 LIMIT :limit
                """
            ),
            {'limit': limit},
        )
        return [
            OrderProblemDTO(
                order_id=r.id,
                status=r.status,
                sku='-',
                amount=r.total_amount,
                age_seconds=r.age,
                detail=f'оплачено={r.paid}, выдано={r.delivered}, возвращено={r.refunded}',
            )
            for r in rows
        ]

    async def stock_drift(self) -> list[str]:
        """reserved_count должен равняться числу позиций в delivering по SKU.

        Расхождение означает утёкший резерв — остаток съеден без выдачи.
        """
        rows = await self._session.execute(
            text(
                """
                SELECT s.sku
                  FROM product_stock s
                  LEFT JOIN (
                        SELECT sku, count(*) AS cnt
                          FROM order_items WHERE status = :status GROUP BY sku
                       ) i ON i.sku = s.sku
                 WHERE s.reserved_count <> COALESCE(i.cnt, 0)
                """
            ),
            {'status': OrderItemStatus.DELIVERING.value},
        )
        return [r.sku for r in rows]
