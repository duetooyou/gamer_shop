"""Наполнение каталога.

    python scripts/seed_catalog.py
    python scripts/seed_catalog.py --generate 5000
"""

import argparse
import asyncio
import json
import random
from pathlib import Path

from sqlalchemy import text

from gamer_shop.infrastructure.config import Config
from gamer_shop.infrastructure.database import new_session_maker

TYPES = ('topup', 'key', 'subscription', 'giftcard')
PREFIX = {'topup': 'TOPUP', 'key': 'KEY', 'subscription': 'SUB', 'giftcard': 'GIFT'}


async def seed(catalog_file: str, stock: int, generate: int, seed_value: int) -> None:
    config = Config()
    maker = new_session_maker(config.postgres)
    catalog = json.loads(Path(catalog_file).read_text(encoding='utf-8'))
    rnd = random.Random(seed_value)

    rows = [
        {
            'sku': p['sku'],
            'name': p['name'],
            'type': p['type'],
            'price': p['price'],
            'currency': p['currency'],
            'image': p.get('image'),
            'stock': stock,
        }
        for p in catalog['products']
    ]

    # Тысячи SKU, чтобы запрос витрины перестал быть тривиальным.
    for i in range(generate):
        ptype = TYPES[i % len(TYPES)]
        rows.append(
            {
                'sku': f'{PREFIX[ptype]}-GEN-{i:06d}',
                'name': f'Тестовый товар {i:06d}',
                'type': ptype,
                'price': rnd.choice([99, 199, 299, 499, 890, 990, 1290, 1990, 3490]),
                'currency': 'RUB',
                'image': None,
                'stock': rnd.randint(0, 25),
            }
        )

    async with maker() as session:
        await session.execute(
            text(
                'INSERT INTO products (sku, name, type, price, currency, image, is_active) '
                'VALUES (:sku, :name, :type, :price, :currency, :image, true) '
                'ON CONFLICT (sku) DO UPDATE SET '
                '  name = EXCLUDED.name, type = EXCLUDED.type, price = EXCLUDED.price'
            ),
            rows,
        )
        await session.execute(
            text(
                'INSERT INTO product_stock (sku, available_count, reserved_count) '
                'VALUES (:sku, :stock, 0) '
                'ON CONFLICT (sku) DO UPDATE SET available_count = EXCLUDED.available_count'
            ),
            rows,
        )
        # Без свежей статистики планировщик выберет seq scan независимо от индексов.
        await session.commit()
        await session.execute(text('ANALYZE products'))
        await session.execute(text('ANALYZE product_stock'))
        await session.commit()

    total = len(rows)
    print(f'Загружено товаров: {total} (из файла: {len(catalog["products"])}, сгенерировано: {generate})')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', default='data/catalog.json')
    parser.add_argument('--stock', type=int, default=5, help='остаток на каждый SKU каталога')
    parser.add_argument('--generate', type=int, default=0, help='сколько SKU сгенерировать')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    asyncio.run(seed(args.catalog, args.stock, args.generate, args.seed))


if __name__ == '__main__':
    main()
