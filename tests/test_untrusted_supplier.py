"""Поставщик, которому нельзя доверять.

Он может втихую отдать один и тот же код дважды, подписать ответ чужим
request_id или сказать «ошибка», выдав код на самом деле. Гарантию, что
покупатель получит ровно один рабочий код, а один код не уйдёт двум
покупателям, приложение обязано держать само.
"""

from sqlalchemy import text

from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import (
    code_of,
    create_order,
    delivered_by,
    drain,
    first_item,
    get_order,
    reconciliation,
    webhook,
)


async def discrepancies(config) -> list[dict]:
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        rows = (
            await session.execute(
                text(
                    'SELECT kind, supplier, state, resolution, code '
                    'FROM supplier_discrepancies ORDER BY id'
                )
            )
        ).mappings().all()
    await maker.kw['bind'].dispose()
    return [dict(r) for r in rows]


async def deliveries_count(config) -> tuple[int, int]:
    """Всего выдач и различных кодов."""
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        total, distinct = (
            await session.execute(
                text('SELECT count(*), count(DISTINCT code) FROM deliveries')
            )
        ).one()
    await maker.kw['bind'].dispose()
    return int(total), int(distinct)


async def test_duplicate_code_never_reaches_two_orders(
    api, container, config, seeded, suppliers
):
    """Поставщик прислал код, уже уехавший другому покупателю."""
    supplier_a, supplier_b = suppliers

    first = await create_order(api, 'KEY-GTA5')
    await webhook(api, first['id'], amount=1990)
    taken = code_of(await get_order(api, first['id']))
    assert taken

    # Дальше A отдаёт этот же код всем подряд.
    supplier_a.set_mode('duplicate')
    second = await create_order(api, 'KEY-GTA5')
    await webhook(api, second['id'], amount=1990)
    await drain(container)

    final = await get_order(api, second['id'])
    # Дубль не принят: позиция ушла к резервному поставщику.
    assert code_of(final) != taken
    assert delivered_by(final) == 'b'

    total, distinct = await deliveries_count(config)
    assert total == distinct, 'один код ушёл в две позиции'

    # Расхождение замечено и разобрано без ручного вмешательства.
    found = [d for d in await discrepancies(config) if d['kind'] == 'duplicate_code']
    assert found, 'дубль кода не попал в журнал расхождений'
    assert found[0]['state'] == 'resolved'
    assert found[0]['code'] == taken


async def test_foreign_response_is_not_taken_as_our_code(
    api, container, config, seeded, suppliers
):
    """Ответ подписан чужим request_id — значит он не про наш запрос."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('foreign_request_id')

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)
    await drain(container)

    final = await get_order(api, order['id'])
    # Код всё равно получен: запрос состояния установил, что именно
    # закреплено за нашим request_id.
    assert code_of(final)
    assert final['status'] == 'delivered'

    found = [d for d in await discrepancies(config) if d['kind'] == 'foreign_response']
    assert found, 'чужая подпись ответа не замечена'
    assert found[0]['state'] == 'resolved'

    # Код, отданный покупателю, действительно числится за нашим запросом.
    truth = supplier_a.stats()['truth']
    assert truth[f'req_{order["id"].removeprefix("ord_")}-1-a'] == code_of(final)


async def test_error_response_with_issued_code_does_not_double_issue(
    api, container, config, seeded, suppliers
):
    """Поставщик ответил ошибкой, хотя код выдал.

    Повтор не должен приводить ко второй выдаче — ни у этого поставщика,
    ни у резервного.
    """
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('lie_error')
    issued_a_before = supplier_a.issued_count()
    issued_b_before = supplier_b.issued_count()

    order = await create_order(api, 'KEY-GTA5')
    await webhook(api, order['id'], amount=1990)
    await drain(container)

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert delivered_by(final) == 'a', 'к резервному идти было незачем: код уже выдан'

    # Ровно одна выдача у A и ни одной у B.
    assert supplier_a.issued_count() - issued_a_before == 1
    assert supplier_b.issued_count() == issued_b_before

    found = [d for d in await discrepancies(config) if d['kind'] == 'phantom_issue']
    assert found, 'ложь про ошибку не замечена'
    assert found[0]['state'] == 'resolved'
    assert found[0]['code'] == code_of(final)

    # Повторное дожатие ничего не меняет и второй выдачи не делает.
    await api.post(f'/admin/orders/{order["id"]}/retry-delivery')
    await drain(container)
    assert code_of(await get_order(api, order['id'])) == code_of(final)
    assert supplier_a.issued_count() - issued_a_before == 1


async def test_customer_gets_exactly_one_code_under_mixed_misbehaviour(
    api, container, config, seeded, suppliers
):
    """Поставщик врёт по-разному, покупатель получает ровно один код."""
    supplier_a, supplier_b = suppliers
    modes = ['lie_error', 'duplicate', 'foreign_request_id', 'ok']

    orders = []
    for mode in modes:
        supplier_a.set_mode(mode)
        order = await create_order(api, 'KEY-GTA5')
        await webhook(api, order['id'], amount=1990)
        orders.append(order['id'])
    supplier_a.set_mode('ok')
    await drain(container, passes=12)

    codes = []
    for order_id in orders:
        final = await get_order(api, order_id)
        item = first_item(final)
        assert item['status'] in ('delivered', 'refunded')
        if item['code']:
            codes.append(item['code'])

    assert len(codes) == len(set(codes)), 'один код ушёл двум покупателям'

    total, distinct = await deliveries_count(config)
    assert total == distinct

    report = await reconciliation(api)
    # Всё замеченное разобрано автоматически.
    assert report['open_discrepancies'] == []
    assert report['discrepancy_counts'], 'недобросовестность прошла незамеченной'
    assert report['money_mismatch'] == []
    assert report['ledger_is_balanced']
