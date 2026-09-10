"""Восстановимые состояния, вебхуки вне порядка, дожатие зависших."""

import asyncio

from dishka import Scope
from sqlalchemy import text

from gamer_shop.application.interactors import ApplyOrphanEventsInteractor
from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import (
    code_of,
    create_order,
    first_item,
    get_order,
    payload,
    webhook,
)



async def test_empty_stock_is_recoverable_not_a_crash(api, seeded, suppliers):
    """paid -> delivering -> out_of_stock -> пополнение -> delivered."""
    # В фикстуре у этого SKU остаток равен единице.
    first = await create_order(api, 'SUB-DISCORD-1M')
    await webhook(api, first['id'], amount=399)
    assert (await get_order(api, first['id']))['status'] == 'delivered'

    second = await create_order(api, 'SUB-DISCORD-1M')
    ack = await webhook(api, second['id'], amount=399)

    assert ack['outcome'] == 'applied'
    stalled = await get_order(api, second['id'])
    # Позиция восстановима, заказ ещё не закрыт: деньги не потеряны.
    assert first_item(stalled)['status'] == 'out_of_stock'
    assert stalled['paid_at'] is not None
    assert code_of(stalled) is None

    refill = await api.post('/admin/stock/SUB-DISCORD-1M/refill', json={'count': 5})
    assert refill.status_code == 201

    await api.post(f'/admin/orders/{second["id"]}/retry-delivery')
    recovered = await get_order(api, second['id'])
    assert recovered['status'] == 'delivered'
    assert code_of(recovered)
    assert code_of(recovered) != code_of(stalled)


async def test_supplier_out_of_stock_is_recoverable(api, seeded, suppliers):
    """Остаток есть у нас, но кода нет у поставщиков."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('out_of_stock')
    supplier_b.set_mode('out_of_stock')

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)

    stalled = await get_order(api, order['id'])
    assert first_item(stalled)['status'] == 'out_of_stock'

    supplier_a.set_mode('ok')
    supplier_b.set_mode('ok')
    await api.post(f'/admin/orders/{order["id"]}/retry-delivery')

    assert (await get_order(api, order['id']))['status'] == 'delivered'


async def test_webhook_arriving_before_order_is_applied_later(
    api, seeded, config, container
):
    """Вебхук пришёл раньше заказа."""
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        next_value = (
            await session.execute(text("SELECT last_value + 1 FROM order_id_seq"))
        ).scalar_one()
    await maker.kw['bind'].dispose()
    future_order_id = f'ord_{next_value:05d}'

    early = await api.post(
        '/webhook/payment', json=payload(future_order_id, 500, event_id='evt_early_1')
    )
    assert early.status_code == 200, 'вебхук вне порядка не должен приводить к ошибке'
    assert early.json()['outcome'] == 'order_not_found'

    # Событие не потеряно — видно в сверке.
    report = (await api.get('/admin/reconciliation', params={'older_than_seconds': 0})).json()
    assert any(e['order_id'] == future_order_id for e in report['unapplied_events'])

    order = await create_order(api, 'STEAM-TOPUP-500')
    assert order['id'] == future_order_id

    async with container(scope=Scope.REQUEST) as rc:
        applied = await (await rc.get(ApplyOrphanEventsInteractor)).execute()
    assert applied == ['evt_early_1']

    paid = await get_order(api, order['id'])
    assert paid['status'] == 'paid'


async def test_late_failed_webhook_does_not_downgrade_delivered_order(api, seeded):
    """Опоздавший failed не откатывает выданный заказ."""
    order = await create_order(api, 'STEAM-TOPUP-500')
    await webhook(api, order['id'], amount=500)
    assert (await get_order(api, order['id']))['status'] == 'delivered'

    ack = await webhook(api, order['id'], amount=500, status='failed')
    assert ack['outcome'] == 'already_final'

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert code_of(final)


async def test_amount_mismatch_is_rejected_and_visible_in_reconciliation(api, seeded):
    """Расхождение суммы не применяется, но не теряется."""
    order = await create_order(api, 'STEAM-TOPUP-500')

    ack = await webhook(api, order['id'], amount=1, event_id='evt_mismatch_1')
    assert ack['outcome'] == 'amount_mismatch'
    assert (await get_order(api, order['id']))['status'] == 'created'

    report = (await api.get('/admin/reconciliation', params={'older_than_seconds': 0})).json()
    problem = next(e for e in report['unapplied_events'] if e['order_id'] == order['id'])
    assert problem['detail'] == 'amount_mismatch'


async def test_background_job_finishes_stuck_order(api, seeded, suppliers):
    """Фоновая задача доводит «зависший» заказ до конца."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('dark', hang_seconds=30)

    # У KEY-GTA5 поставщик A — тот, что замолчал.
    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)
    assert (await get_order(api, order['id']))['status'] == 'delivering'

    issued_before = supplier_a.issued_count()
    supplier_a.set_mode('ok')
    await asyncio.sleep(1.2)  # порог «зависшей» позиции в тестах — 1 с

    response = await api.post('/admin/retry-stuck')
    assert response.status_code == 201
    outcomes = {o['order_item_id']: o['result'] for o in response.json()['outcomes']}
    assert outcomes[f'{order["id"]}-1'] == 'delivered'

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    # Дожатие идёт тем же request_id — второго кода не появилось.
    assert supplier_a.issued_count() == issued_before
