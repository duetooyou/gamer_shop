"""EXPLAIN ANALYZE запроса витрины и, для сравнения, наивных вариантов.

    python scripts/seed_catalog.py --generate 200000
    python scripts/explain_storefront.py
"""

import asyncio

from sqlalchemy import text

from gamer_shop.infrastructure.config import Config
from gamer_shop.infrastructure.database import new_session_maker

STOREFRONT = """
SELECT p.sku, p.name, p.type, p.price, p.currency, s.available_count
FROM (
    SELECT sku, name, type, price, currency
    FROM products
    WHERE is_active AND type = 'key' AND sku > 'BULK-KEY-0100000'
    ORDER BY sku
    LIMIT 50
) p
JOIN product_stock s ON s.sku = p.sku
ORDER BY p.sku
"""

FLAT_JOIN = """
SELECT p.sku, p.name, p.type, p.price, p.currency, s.available_count
FROM products p
JOIN product_stock s ON s.sku = p.sku
WHERE p.is_active AND p.type = 'key' AND p.sku > 'BULK-KEY-0100000'
ORDER BY p.sku
LIMIT 50
"""

NAIVE_OFFSET = """
SELECT p.sku, p.name, p.type, p.price, p.currency, s.available_count
FROM products p
JOIN product_stock s ON s.sku = p.sku
WHERE p.is_active AND p.type = 'key'
ORDER BY p.sku
OFFSET 25000 LIMIT 50
"""

QUERIES = {
    'Витрина: страница сначала, остаток потом (как в коде)': STOREFRONT,
    'Плоский join: планировщик уходит в merge join': FLAT_JOIN,
    'Наивный вариант: OFFSET вместо курсора': NAIVE_OFFSET,
}


async def main() -> None:
    maker = new_session_maker(Config().postgres)
    async with maker() as session:
        total = (await session.execute(text('SELECT count(*) FROM products'))).scalar_one()
        print(f'Товаров в каталоге: {total}\n')
        for title, sql in QUERIES.items():
            print('=' * 78)
            print(title)
            print('=' * 78)
            rows = (
                await session.execute(text(f'EXPLAIN (ANALYZE, BUFFERS) {sql}'))
            ).scalars()
            for row in rows:
                print(row)
            print()
    await maker.kw['bind'].dispose()


if __name__ == '__main__':
    asyncio.run(main())
