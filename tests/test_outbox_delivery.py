"""Выдача не зависит от очереди задач.

Первый этап ставил задачу выдачи после коммита вебхука: смерть процесса
между коммитом и постановкой теряла выдачу по оплаченному заказу. Теперь
команда пишется в ту же транзакцию, и потерянная побудка ничего не меняет —
только задержку.
"""

import asyncio

from sqlalchemy import text

from gamer_shop.application.interactors import OutboxRelayInteractor
from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import create_order, get_order, webhook


async def outbox_rows(config) -> list[dict]:
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        rows = (
            await session.execute(
                text('SELECT kind, dedup_key, state, priority FROM outbox ORDER BY id')
            )
        ).mappings().all()
    await maker.kw['bind'].dispose()
    return [dict(r) for r in rows]


async def test_command_is_written_in_the_webhook_transaction(
    silent_api, config, seeded
):
    order = await create_order(silent_api, 'KEY-GTA5')
    await webhook(silent_api, order['id'], order['total_amount'])

    # Побудка не дошла — выдачи ещё нет, но команда уже записана.
    assert (await get_order(silent_api, order['id']))['status'] == 'paid'
    assert await outbox_rows(config) == [
        {
            'kind': 'deliver_item',
            'dedup_key': f'{order["id"]}-1',
            'state': 'pending',
            'priority': 10,
        }
    ]


async def test_relay_delivers_without_any_notification(
    silent_api, silent_container, config, seeded, suppliers
):
    order = await create_order(silent_api, 'KEY-GTA5')
    await webhook(silent_api, order['id'], order['total_amount'])
    assert (await get_order(silent_api, order['id']))['status'] == 'paid'

    # Плановый проход релея — единственное, что реально требуется.
    async with silent_container() as scope:
        relay = await scope.get(OutboxRelayInteractor)
        assert await relay.run_once() == 1

    # Выдача породила команду расчёта — крутим до тишины.
    async with silent_container() as scope:
        relay = await scope.get(OutboxRelayInteractor)
        while await relay.run_once():
            pass

    delivered = await get_order(silent_api, order['id'])
    assert delivered['status'] == 'delivered'
    assert delivered['items'][0]['code']
    assert {r['state'] for r in await outbox_rows(config)} == {'done'}


async def test_repeated_relay_pass_does_not_deliver_twice(
    silent_api, silent_container, config, seeded, suppliers
):
    a, b = suppliers
    # Заглушки помнят выданное между тестами — считаем прирост.
    issued_before = a.issued_count() + b.issued_count()

    order = await create_order(silent_api, 'KEY-GTA5')
    await webhook(silent_api, order['id'], order['total_amount'])

    async with silent_container() as scope:
        relay = await scope.get(OutboxRelayInteractor)
        assert await relay.run_once() == 1
        # Осталась только команда расчёта; выдача повторно не пойдёт.
        while await relay.run_once():
            pass

    final = await get_order(silent_api, order['id'])
    assert final['status'] == 'delivered'
    assert final['items'][0]['code']
    assert a.issued_count() + b.issued_count() - issued_before == 1


async def test_duplicate_webhook_does_not_queue_second_command(
    silent_api, config, seeded
):
    order = await create_order(silent_api, 'KEY-GTA5')
    event_id = 'evt_fixed_for_dedup'
    await webhook(silent_api, order['id'], order['total_amount'], event_id=event_id)
    await webhook(silent_api, order['id'], order['total_amount'], event_id=event_id)

    assert len(await outbox_rows(config)) == 1


async def test_unknown_command_kind_ends_up_dead_and_visible(
    silent_api, silent_container, config, seeded
):
    """Команда, записанная другой версией кода, не роняет разбор пачки:
    она уходит в dead и попадает в сверку, а не теряется молча."""
    from tests.helpers import reconciliation

    maker = new_session_maker(config.postgres)
    async with maker() as session:
        await session.execute(
            text(
                "INSERT INTO outbox (kind, dedup_key, payload) "
                "VALUES ('from_the_future', 'ord_00001', '{}'::jsonb)"
            )
        )
        await session.commit()
    await maker.kw['bind'].dispose()

    async with silent_container() as scope:
        relay = await scope.get(OutboxRelayInteractor)
        # Между попытками команда лежит под бэкоффом — даём ей всплыть.
        for _ in range(20):
            await relay.run_once()
            await asyncio.sleep(0.12)

    report = await reconciliation(silent_api)
    dead = report['dead_commands']
    assert [c['kind'] for c in dead] == ['from_the_future']
    assert dead[0]['attempts'] >= 1
