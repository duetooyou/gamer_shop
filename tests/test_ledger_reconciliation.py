"""Журнал денежных движений и сверка."""

import asyncio
import random

import pytest
from sqlalchemy import text

from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import create_order, get_order, payload, reconciliation, webhook



async def _ledger_rows(config, order_id: str) -> list[tuple[str, str, int]]:
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        rows = (
            await session.execute(
                text(
                    'SELECT account, direction, amount FROM ledger_entries '
                    'WHERE order_id = :id ORDER BY created_at, account'
                ),
                {'id': order_id},
            )
        ).all()
    await maker.kw['bind'].dispose()
    return [(r.account, r.direction, r.amount) for r in rows]


async def test_ledger_records_payment_and_delivery_as_double_entry(api, seeded, config):
    order = await create_order(api, 'STEAM-TOPUP-500')
    await webhook(api, order['id'], amount=500)
    assert (await get_order(api, order['id']))['status'] == 'delivered'

    rows = await _ledger_rows(config, order['id'])
    # Две операции по две строки: приняли деньги, закрыли обязательство.
    assert sorted(rows) == sorted(
        [
            ('cash_in', 'debit', 500),
            ('customer_liability', 'credit', 500),
            ('customer_liability', 'debit', 500),
            ('revenue', 'credit', 500),
        ]
    )
    assert sum(a for _, d, a in rows if d == 'debit') == sum(
        a for _, d, a in rows if d == 'credit'
    )


async def test_paid_but_undelivered_order_keeps_open_liability(api, seeded, suppliers, config):
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('fail')
    supplier_b.set_mode('fail')

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)
    assert (await get_order(api, order['id']))['status'] == 'delivery_failed'

    rows = await _ledger_rows(config, order['id'])
    # Только оплата: выручку не признаём, пока товар не выдан.
    assert rows == [('cash_in', 'debit', 1990), ('customer_liability', 'credit', 1990)]

    report = await reconciliation(api)
    assert any(p['order_id'] == order['id'] for p in report['paid_not_delivered'])
    liability = next(b for b in report['ledger_balances'] if b['account'] == 'customer_liability')
    assert liability['balance'] == -1990


async def test_ledger_always_balances_after_chaotic_run(api, seeded, suppliers, config):
    """Хаос-прогон: случайные отказы, таймауты, дубли и гонки.

    Что бы ни произошло, журнал обязан сойтись.
    """
    supplier_a, supplier_b = suppliers
    rnd = random.Random(20250901)
    skus = {'STEAM-TOPUP-500': 500, 'KEY-CS2-PRIME': 1290, 'KEY-GTA5': 1990}

    for _ in range(12):
        supplier_a.set_mode(rnd.choice(['ok', 'fail', 'timeout', 'out_of_stock']), hang_seconds=2)
        supplier_b.set_mode(rnd.choice(['ok', 'fail', 'ok']))

        sku = rnd.choice(list(skus))
        order = await create_order(api, sku)
        # Дубли и параллельные доставки одного события.
        body = payload(order['id'], skus[sku])
        await asyncio.gather(
            *(api.post('/webhook/payment', json=body) for _ in range(rnd.randint(1, 5))),
            return_exceptions=True,
        )

    supplier_a.set_mode('ok')
    supplier_b.set_mode('ok')

    report = await reconciliation(api)
    assert report['ledger_is_balanced'], report['ledger_balances']
    assert sum(b['balance'] for b in report['ledger_balances']) == 0
    assert report['delivered_not_paid'] == [], 'товар выдан по неоплаченному заказу'
    assert report['stock_drift'] == [], 'резерв остатка разошёлся с числом выдач'

    maker = new_session_maker(config.postgres)
    async with maker() as session:
        total, distinct = (
            await session.execute(
                text('SELECT count(*), count(DISTINCT code) FROM deliveries')
            )
        ).one()
        orders_with_two = (
            await session.execute(
                text(
                    'SELECT count(*) FROM ('
                    '  SELECT order_id FROM deliveries GROUP BY order_id HAVING count(*) > 1'
                    ') t'
                )
            )
        ).scalar_one()
    await maker.kw['bind'].dispose()

    assert total == distinct, 'один код ушёл в два заказа'
    assert orders_with_two == 0, 'по одному заказу больше одной выдачи'


async def test_reconciliation_reports_stuck_and_unresolved(api, seeded, suppliers):
    supplier_a, _ = suppliers
    supplier_a.set_mode('timeout', hang_seconds=30)

    order = await create_order(api, 'KEY-CS2-PRIME')
    await webhook(api, order['id'], amount=1290)

    report = await reconciliation(api)
    assert any(p['order_id'] == order['id'] for p in report['stuck_delivering'])
    unresolved = next(
        p for p in report['unresolved_supplier_requests'] if p['order_id'] == order['id']
    )
    assert unresolved['status'] == 'supplier_a:unknown'
    assert report['ledger_is_balanced']
