"""Каталог под нагрузкой.

Тесты меряют не секунды, а форму плана: на тысячах строк запрос обязан идти
индексом и не читать таблицу целиком.
"""

import json

import pytest
from sqlalchemy import text

from gamer_shop.infrastructure.database import new_session_maker


CATALOG_SIZE = 5000

# Карточка плюс остаток, keyset-пагинация.
STOREFRONT_SQL = """
SELECT p.sku, p.name, p.type, p.price, p.currency, s.available_count
FROM products p
JOIN product_stock s ON s.sku = p.sku
WHERE p.is_active AND p.type = :type AND p.sku > :cursor
ORDER BY p.sku
LIMIT 50
"""


@pytest.fixture
async def large_catalog(config, clean_db):
    """Тысячи SKU одним запросом, генерация на стороне БД."""
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO products (sku, name, type, price, currency, is_active)
                SELECT
                    'SKU-' || (ARRAY['TOPUP','KEY','SUB','GIFT'])[1 + i % 4]
                            || '-' || lpad(i::text, 6, '0'),
                    'Товар ' || i,
                    (ARRAY['topup','key','subscription','giftcard'])[1 + i % 4],
                    100 + (i % 40) * 50,
                    'RUB',
                    i % 20 <> 0            -- каждый двадцатый снят с продажи
                FROM generate_series(1, :n) AS i
                """
            ),
            {'n': CATALOG_SIZE},
        )
        await session.execute(
            text(
                'INSERT INTO product_stock (sku, available_count) '
                'SELECT sku, (random() * 30)::int FROM products'
            )
        )
        await session.commit()
        # Без свежей статистики планировщик выберет seq scan.
        await session.execute(text('ANALYZE products'))
        await session.execute(text('ANALYZE product_stock'))
        await session.commit()
    yield maker
    await maker.kw['bind'].dispose()


async def _explain(maker, sql: str, params: dict) -> dict:
    async with maker() as session:
        raw = (
            await session.execute(
                text(f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}'), params
            )
        ).scalar_one()
    return raw[0] if isinstance(raw, list) else json.loads(raw)[0]


def _node_types(node: dict) -> list[str]:
    types = [node['Node Type']]
    for child in node.get('Plans', []):
        types.extend(_node_types(child))
    return types


async def test_storefront_query_uses_index_not_full_scan(large_catalog):
    plan = await _explain(
        large_catalog, STOREFRONT_SQL, {'type': 'key', 'cursor': 'SKU-KEY-000000'}
    )
    nodes = _node_types(plan['Plan'])

    assert 'Seq Scan' not in nodes, nodes
    assert any('Index' in n for n in nodes), nodes
    # Покрывающий индекс отдаёт все колонки — heap не нужен.
    assert 'Index Only Scan' in nodes, nodes


async def test_storefront_reads_only_a_page_worth_of_rows(large_catalog):
    plan = await _explain(
        large_catalog, STOREFRONT_SQL, {'type': 'key', 'cursor': 'SKU-KEY-000000'}
    )

    # Индекс отдаёт строки уже в нужном порядке, LIMIT обрывает чтение сразу.
    assert plan['Plan']['Actual Rows'] <= 50
    assert plan['Plan']['Actual Total Time'] < 100


async def test_deep_page_costs_the_same_as_first(large_catalog):
    """Глубокая страница не дороже первой. С OFFSET время росло бы линейно."""
    first = await _explain(
        large_catalog, STOREFRONT_SQL, {'type': 'key', 'cursor': ''}
    )
    deep = await _explain(
        large_catalog, STOREFRONT_SQL, {'type': 'key', 'cursor': 'SKU-KEY-004800'}
    )

    assert deep['Plan']['Actual Rows'] <= 50
    # Разброс в разы на прогретом кэше возможен, на порядок — нет.
    assert deep['Plan']['Actual Total Time'] < max(first['Plan']['Actual Total Time'], 1.0) * 10


async def test_inactive_products_are_excluded_from_index(large_catalog):
    """Частичный индекс не хранит снятые с продажи."""
    async with large_catalog() as session:
        inactive = (
            await session.execute(text('SELECT count(*) FROM products WHERE NOT is_active'))
        ).scalar_one()
        index_rows = (
            await session.execute(
                text(
                    "SELECT idx_tup_read FROM pg_stat_user_indexes "
                    "WHERE indexrelname = 'ix_products_storefront'"
                )
            )
        ).scalar_one_or_none()

    assert inactive > 0, 'в наборе должны быть неактивные товары'
    assert index_rows is not None, 'индекс витрины отсутствует'
