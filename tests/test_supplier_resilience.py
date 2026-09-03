"""Таймауты, повторы и фолбэк на резервного поставщика."""

import asyncio

import pytest
from sqlalchemy import text

from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import create_order, get_order, webhook



async def _supplier_request_rows(config, order_id: str) -> dict[str, tuple[str, str | None]]:
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        rows = (
            await session.execute(
                text(
                    'SELECT supplier, state, code, request_id FROM supplier_requests '
                    'WHERE order_id = :id'
                ),
                {'id': order_id},
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
    assert rows['a'][2] == f'req_{suffix}-1'


async def test_timeout_after_issue_does_not_double_issue(api, seeded, suppliers, config):
    """Поставщик выдал код, но ответ не дошёл.

    Заглушка сначала выдаёт и запоминает код, и только потом зависает.
    """
    supplier_a, supplier_b = suppliers
    # Зависание длиннее таймаута клиента и всех повторов.
    supplier_a.set_mode('timeout', hang_seconds=30)
    issued_a_before = supplier_a.issued_count()
    issued_b_before = supplier_b.issued_count()

    order = await create_order(api, 'KEY-CS2-PRIME')
    await webhook(api, order['id'], amount=1290)

    # Судьба кода неизвестна — заказ остаётся в delivering.
    current = await get_order(api, order['id'])
    assert current['status'] == 'delivering'
    assert current['code'] is None

    # A выдал ровно один код, несмотря на несколько попыток.
    assert supplier_a.issued_count() - issued_a_before == 1
    # К резервному не пошли: таймаут — не отказ.
    assert supplier_b.issued_count() == issued_b_before

    rows = await _supplier_request_rows(config, order['id'])
    assert rows['a'][0] == 'unknown'
    assert 'b' not in rows

    # Поставщик ожил: повтор тем же request_id получает тот же код.
    supplier_a.set_mode('ok')
    await asyncio.sleep(1.2)  # переждать порог «зависшего» заказа
    retry = await api.post(f'/admin/orders/{order["id"]}/retry-delivery')
    assert retry.status_code == 201

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert final['supplier'] == 'a'
    assert supplier_a.issued_count() - issued_a_before == 1
    assert supplier_b.issued_count() == issued_b_before

    request_id = rows['a'][2]
    assert supplier_a.stats()['issued'][request_id] == final['code']


async def test_unknown_timeout_never_falls_back_to_backup(api, seeded, suppliers):
    """Из неопределённости фолбэк запрещён: иначе будет две выдачи."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('timeout', hang_seconds=30)
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
    assert final['supplier'] == 'b'

    assert supplier_a.issued_count() == issued_a_before
    assert supplier_b.issued_count() - issued_b_before == 1

    rows = await _supplier_request_rows(config, order['id'])
    assert rows['a'][0] == 'refused'
    assert rows['b'][0] == 'ok'
    suffix = order['id'].removeprefix('ord_')
    assert rows['b'][2] == f'req_{suffix}-2'


async def test_both_suppliers_down_leaves_recoverable_state(api, seeded, suppliers):
    """Оба отказали — заказ восстановим, падения нет."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('fail')
    supplier_b.set_mode('fail')

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivery_failed'
    assert final['code'] is None

    supplier_a.set_mode('ok')
    supplier_b.set_mode('ok')
    await api.post(f'/admin/orders/{order["id"]}/retry-delivery')

    recovered = await get_order(api, order['id'])
    assert recovered['status'] == 'delivered'
    assert recovered['code']


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
    taken_code = (await get_order(api, donor['id']))['code']

    victim = await create_order(api, 'KEY-GTA5')
    # Код, который вернёт A, уже занят другим заказом.
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        await session.execute(
            text("UPDATE orders SET status = 'paid', paid_at = now() WHERE id = :id"),
            {'id': victim['id']},
        )
        suffix = victim['id'].removeprefix('ord_')
        await session.execute(
            text(
                'INSERT INTO supplier_requests '
                '(request_id, order_id, supplier, sku, state, code, attempts) '
                "VALUES (:rid, :oid, 'a', 'KEY-GTA5', 'ok', :code, 1)"
            ),
            {'rid': f'req_{suffix}-1', 'oid': victim['id'], 'code': taken_code},
        )
        await session.commit()
    await maker.kw['bind'].dispose()

    issued_b_before = supplier_b.issued_count()

    await api.post(f'/admin/orders/{victim["id"]}/retry-delivery')

    final = await get_order(api, victim['id'])
    assert final['status'] == 'delivered'
    assert final['supplier'] == 'b'
    assert final['code'] != taken_code
    assert supplier_b.issued_count() - issued_b_before == 1

    rows = await _supplier_request_rows(config, victim['id'])
    assert rows['a'][0] == 'refused'
    assert rows['b'][0] == 'ok'
