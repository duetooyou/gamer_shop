"""Exactly-once под гонками."""

import asyncio
from collections import Counter
from uuid import uuid4

import pytest
from sqlalchemy import text

from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import code_of, create_order, get_order, payload


PARALLEL = 50


async def _count(config, sql: str, params: dict) -> int:
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        value = (await session.execute(text(sql), params)).scalar_one()
    await maker.kw['bind'].dispose()
    return int(value)


async def test_fifty_parallel_webhooks_deliver_exactly_once(
    api, seeded, suppliers, config
):
    """50 одновременных «оплачено» — ровно одна выдача."""
    supplier_a, supplier_b = suppliers
    issued_before = supplier_a.issued_count() + supplier_b.issued_count()
    order = await create_order(api, 'STEAM-TOPUP-500')

    # Разные event_id: 50 самостоятельных событий.
    responses = await asyncio.gather(
        *(
            api.post('/webhook/payment', json=payload(order['id'], 500))
            for _ in range(PARALLEL)
        )
    )

    # Все 200: 5xx заставил бы платёжку повторять доставку.
    assert [r.status_code for r in responses] == [200] * PARALLEL

    outcomes = Counter(r.json()['outcome'] for r in responses)
    assert outcomes['applied'] == 1
    assert outcomes['already_final'] == PARALLEL - 1

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert code_of(final)

    # Одна строка выдачи — ограничение БД, а не соглашение в коде.
    assert await _count(
        config,
        'SELECT count(*) FROM deliveries WHERE order_item_id = :id',
        {'id': f'{order["id"]}-1'},
    ) == 1

    issued_after = supplier_a.issued_count() + supplier_b.issued_count()
    assert issued_after - issued_before == 1

    # По две строки на оплату и на выдачу.
    assert await _count(
        config, 'SELECT count(*) FROM ledger_entries WHERE order_id = :id', {'id': order['id']}
    ) == 4


async def test_same_event_id_repeated_changes_nothing(api, seeded, suppliers, config):
    """Повтор с тем же event_id ничего не меняет."""
    supplier_a, supplier_b = suppliers
    issued_before = supplier_a.issued_count() + supplier_b.issued_count()
    order = await create_order(api, 'KEY-CS2-PRIME')
    event_id = f'evt_{uuid4().hex[:12]}'

    responses = await asyncio.gather(
        *(
            api.post(
                '/webhook/payment', json=payload(order['id'], 1290, event_id=event_id)
            )
            for _ in range(PARALLEL)
        )
    )

    assert [r.status_code for r in responses] == [200] * PARALLEL
    outcomes = Counter(r.json()['outcome'] for r in responses)
    assert outcomes['applied'] == 1
    assert outcomes['duplicate'] == PARALLEL - 1

    # Событие в журнале одно — сработал уникальный ключ.
    assert await _count(
        config, 'SELECT count(*) FROM payment_events WHERE event_id = :e', {'e': event_id}
    ) == 1
    assert supplier_a.issued_count() + supplier_b.issued_count() - issued_before == 1

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'


async def test_parallel_delivery_attempts_produce_one_delivery(
    api, seeded, suppliers, config, container
):
    """Параллельные воркеры по одному заказу: выдача всё равно одна."""
    from dishka import Scope

    from gamer_shop.application.enums import OrderStatus
    from gamer_shop.application.exceptions import ApplicationException
    from gamer_shop.application.interactors import (
        DeliverOrderItemInteractor,
        SettleOrderInteractor,
    )

    supplier_a, supplier_b = suppliers
    issued_before = supplier_a.issued_count() + supplier_b.issued_count()
    order = await create_order(api, 'KEY-GTA5')

    # Оплачиваем напрямую, чтобы выдача не стартовала раньше времени.
    item_id = f'{order["id"]}-1'
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        await session.execute(
            text("UPDATE orders SET status = 'paid', paid_at = now() WHERE id = :id"),
            {'id': order['id']},
        )
        await session.execute(
            text("UPDATE order_items SET status = 'paid' WHERE id = :id"),
            {'id': item_id},
        )
        await session.commit()
    await maker.kw['bind'].dispose()

    async def deliver() -> str:
        async with container(scope=Scope.REQUEST) as rc:
            interactor = await rc.get(DeliverOrderItemInteractor)
            try:
                result = await interactor.execute(item_id)
            except ApplicationException:
                return 'pending'
            return result.kind.value

    results = Counter(await asyncio.gather(*(deliver() for _ in range(20))))

    assert results['delivered'] == 1
    assert results['delivered'] + results['skipped'] + results['already_delivered'] == 20

    assert await _count(
        config,
        'SELECT count(*) FROM deliveries WHERE order_item_id = :id',
        {'id': item_id},
    ) == 1
    assert supplier_a.issued_count() + supplier_b.issued_count() - issued_before == 1

    async with container(scope=Scope.REQUEST) as rc:
        await (await rc.get(SettleOrderInteractor)).execute(order['id'])
    assert (await get_order(api, order['id']))['status'] == OrderStatus.DELIVERED.value
