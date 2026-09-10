"""Таймауты, повторы и фолбэк на резервного поставщика."""

import asyncio

from sqlalchemy import text

from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import (
    code_of,
    create_order,
    delivered_by,
    first_item,
    get_order,
    webhook,
)



async def _supplier_request_rows(config, order_id: str) -> dict[str, tuple[str, str | None]]:
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        rows = (
            await session.execute(
                text(
                    'SELECT supplier, state, code, request_id FROM supplier_requests '
                    'WHERE order_item_id = :id'
                ),
                {'id': f'{order_id}-1'},
            )
        ).all()
    await maker.kw['bind'].dispose()
    return {r.supplier: (r.state, r.code, r.request_id) for r in rows}


async def test_request_id_is_derived_from_order_not_from_attempt(api, seeded, config):
    """Если бы request_id зависел от номера попытки, повтор после таймаута
    попал бы в новый идентификатор и поставщик выдал бы второй код."""
    order = await create_order(api, 'STEAM-TOPUP-500')
    await webhook(api, order['id'], amount=500)

    rows = await _supplier_request_rows(config, order['id'])
    suffix = order['id'].removeprefix('ord_')
    # Идентификатор выведен из позиции и поставщика, а не из номера попытки.
    assert rows['a'][2] == f'req_{suffix}-1-a'


async def test_timeout_after_issue_does_not_double_issue(api, seeded, suppliers, config):
    """Поставщик выдал код, но ответ не дошёл.

    Заглушка сначала выдаёт и запоминает код, и только потом зависает.
    Запрос состояния находит этот код, и позиция закрывается им же —
    вместо второй выдачи.
    """
    supplier_a, supplier_b = suppliers
    # Зависание длиннее таймаута клиента.
    supplier_a.set_mode('timeout', hang_seconds=30)
    issued_a_before = supplier_a.issued_count()
    issued_b_before = supplier_b.issued_count()

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)

    # Ответа не было, но код у поставщика есть — запрос состояния его нашёл.
    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert delivered_by(final) == 'a'

    # A выдал ровно один код.
    assert supplier_a.issued_count() - issued_a_before == 1
    # К резервному не пошли: молчание — не отказ.
    assert supplier_b.issued_count() == issued_b_before

    rows = await _supplier_request_rows(config, order['id'])
    assert rows['a'][0] == 'ok'
    assert 'b' not in rows
    assert supplier_a.stats()['issued'][rows['a'][2]] == code_of(final)


async def test_silent_supplier_leaves_unknown_and_blocks_fallback(
    api, seeded, suppliers, config
):
    """Поставщик молчит и на выдачу, и на запрос состояния.

    Единственный случай, когда неопределённость сохраняется: правды узнать
    негде, и уходить к резервному нельзя — код мог быть выдан.
    """
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('dark', hang_seconds=30)
    issued_a_before = supplier_a.issued_count()
    issued_b_before = supplier_b.issued_count()

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)

    current = await get_order(api, order['id'])
    assert current['status'] == 'delivering'
    assert first_item(current)['status'] == 'delivering'
    assert code_of(current) is None

    assert supplier_a.issued_count() - issued_a_before == 1
    assert supplier_b.issued_count() == issued_b_before

    rows = await _supplier_request_rows(config, order['id'])
    assert rows['a'][0] == 'unknown'
    assert 'b' not in rows

    # Поставщик ожил: повтор тем же request_id получает тот же код.
    supplier_a.set_mode('ok')
    await asyncio.sleep(1.2)  # переждать порог «зависшей» позиции
    retry = await api.post(f'/admin/orders/{order["id"]}/retry-delivery')
    assert retry.status_code == 201

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert delivered_by(final) == 'a'
    assert supplier_a.issued_count() - issued_a_before == 1
    assert supplier_b.issued_count() == issued_b_before
    assert supplier_a.stats()['issued'][rows['a'][2]] == code_of(final)


async def test_unknown_timeout_never_falls_back_to_backup(api, seeded, suppliers):
    """Из неопределённости фолбэк запрещён: иначе будет две выдачи."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('dark', hang_seconds=30)
    issued_b_before = supplier_b.issued_count()

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)

    assert supplier_b.issued_count() == issued_b_before, (
        'к резервному поставщику обратились после таймаута основного — '
        'это привело бы к двойной выдаче'
    )


async def test_primary_unavailable_falls_back_to_backup_once(
    api, seeded, suppliers, config
):
    """A недоступен, фолбэк на B, выдача ровно одна."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('fail')  # 5xx: ответ завершён, код точно не выдан
    issued_a_before = supplier_a.issued_count()
    issued_b_before = supplier_b.issued_count()

    order = await create_order(api, 'STEAM-TOPUP-500')
    await webhook(api, order['id'], amount=500)

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert delivered_by(final) == 'b'

    assert supplier_a.issued_count() == issued_a_before
    assert supplier_b.issued_count() - issued_b_before == 1

    rows = await _supplier_request_rows(config, order['id'])
    assert rows['a'][0] == 'refused'
    assert rows['b'][0] == 'ok'
    suffix = order['id'].removeprefix('ord_')
    assert rows['b'][2] == f'req_{suffix}-1-b'


async def test_both_suppliers_down_leaves_recoverable_state(api, seeded, suppliers):
    """Оба отказали — позиция восстановима, падения нет."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('fail')
    supplier_b.set_mode('fail')

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)

    final = await get_order(api, order['id'])
    assert first_item(final)['status'] == 'delivery_failed'
    assert code_of(final) is None

    supplier_a.set_mode('ok')
    supplier_b.set_mode('ok')
    await api.post(f'/admin/orders/{order["id"]}/retry-delivery')

    recovered = await get_order(api, order['id'])
    assert recovered['status'] == 'delivered'
    assert code_of(recovered)


async def test_code_already_used_falls_back_instead_of_looping(
    api, seeded, suppliers, config
):
    """Поставщик вернул код, уже закреплённый за другим заказом.

    UNIQUE(deliveries.code) не даёт ключу уйти в два заказа, но заказ не должен
    из-за этого зациклиться: request_id детерминирован, и поставщик будет
    возвращать тот же непригодный код на каждом повторе.
    """
    supplier_a, supplier_b = suppliers
    donor = await create_order(api, 'STEAM-TOPUP-500')
    await webhook(api, donor['id'], amount=500)
    taken_code = code_of(await get_order(api, donor['id']))

    victim = await create_order(api, 'KEY-GTA5')
    # Код, который вернёт A, уже занят другим заказом.
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        await session.execute(
            text("UPDATE orders SET status = 'paid', paid_at = now() WHERE id = :id"),
            {'id': victim['id']},
        )
        await session.execute(
            text("UPDATE order_items SET status = 'paid' WHERE order_id = :id"),
            {'id': victim['id']},
        )
        suffix = victim['id'].removeprefix('ord_')
        await session.execute(
            text(
                'INSERT INTO supplier_requests '
                '(request_id, order_item_id, supplier, sku, state, code, attempts) '
                "VALUES (:rid, :iid, 'a', 'KEY-GTA5', 'ok', :code, 1)"
            ),
            {
                'rid': f'req_{suffix}-1-a',
                'iid': f'{victim["id"]}-1',
                'code': taken_code,
            },
        )
        await session.commit()
    await maker.kw['bind'].dispose()

    issued_b_before = supplier_b.issued_count()

    await api.post(f'/admin/orders/{victim["id"]}/retry-delivery')

    final = await get_order(api, victim['id'])
    assert final['status'] == 'delivered'
    assert delivered_by(final) == 'b'
    assert code_of(final) != taken_code
    assert supplier_b.issued_count() - issued_b_before == 1

    rows = await _supplier_request_rows(config, victim['id'])
    assert rows['a'][0] == 'refused'
    assert rows['b'][0] == 'ok'
