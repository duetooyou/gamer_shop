"""Заказ из нескольких товаров, где часть не выдаётся.

Главное требование этапа: что смогли выдать — остаётся у покупателя, за что
не смогли — деньги возвращаются, и по деньгам сходится при любом сбое и
повторе.
"""

from sqlalchemy import text

from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import (
    create_multi_order,
    drain,
    get_order,
    item_statuses,
    reconciliation,
    webhook,
)


async def money(config, order_id: str) -> tuple[int, int, int]:
    """Оплачено, выдано, возвращено — из проводок, а не из статусов."""
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT
                      COALESCE(sum(amount) FILTER (
                          WHERE account = 'cash_in' AND direction = 'debit'), 0) AS paid,
                      COALESCE(sum(amount) FILTER (
                          WHERE account = 'revenue' AND direction = 'credit'), 0)
                          AS delivered,
                      COALESCE(sum(amount) FILTER (
                          WHERE account = 'refund_payable' AND direction = 'credit'), 0)
                          AS refunded
                    FROM ledger_entries WHERE order_id = :id
                    """
                ),
                {'id': order_id},
            )
        ).one()
    await maker.kw['bind'].dispose()
    return int(row.paid), int(row.delivered), int(row.refunded)


async def test_partial_failure_keeps_delivered_and_refunds_the_rest(
    api, container, config, seeded, payments
):
    """Остатка хватает на одну единицу из двух: одна выдана, за вторую возврат."""
    # У SUB-DISCORD-1M остаток равен единице.
    order = await create_multi_order(
        api, ('STEAM-TOPUP-500', 1), ('SUB-DISCORD-1M', 2)
    )
    assert order['total_amount'] == 500 + 399 * 2

    refunds_before = payments.refund_count()
    await webhook(api, order['id'], amount=order['total_amount'])
    await drain(container)

    final = await get_order(api, order['id'])
    assert final['status'] == 'partially_delivered'
    assert sorted(item_statuses(final)) == ['delivered', 'delivered', 'refunded']

    delivered_items = [i for i in final['items'] if i['status'] == 'delivered']
    refunded_items = [i for i in final['items'] if i['status'] == 'refunded']
    # Выданное остаётся у покупателя.
    assert all(i['code'] for i in delivered_items)
    # За невыданное кода нет, но есть отметка возврата.
    assert refunded_items[0]['code'] is None
    assert refunded_items[0]['refunded_at'] is not None

    paid, delivered, refunded = await money(config, order['id'])
    assert paid == 500 + 399 * 2
    assert delivered == 500 + 399
    assert refunded == 399
    assert paid == delivered + refunded

    # Возврат ушёл в платёжную систему ровно один.
    assert payments.refund_count() - refunds_before == 1


async def test_nothing_delivered_ends_as_refunded(
    api, container, config, seeded, suppliers, payments
):
    """Оба поставщика молчат — заказ закрывается возвратом целиком."""
    supplier_a, supplier_b = suppliers
    supplier_a.set_mode('fail')
    supplier_b.set_mode('fail')

    order = await create_multi_order(api, ('KEY-GTA5', 1), ('KEY-CS2-PRIME', 1))
    await webhook(api, order['id'], amount=order['total_amount'])
    await drain(container, passes=10)

    final = await get_order(api, order['id'])
    assert final['status'] == 'refunded'
    assert item_statuses(final) == ['refunded', 'refunded']

    paid, delivered, refunded = await money(config, order['id'])
    assert delivered == 0
    assert paid == refunded == order['total_amount']


async def test_repeated_relay_passes_do_not_duplicate_refunds(
    api, container, config, seeded, payments
):
    """Повтор любого шага не создаёт ни лишних выдач, ни лишних возвратов."""
    order = await create_multi_order(api, ('SUB-DISCORD-1M', 2))
    refunds_before = payments.refund_count()

    await webhook(api, order['id'], amount=order['total_amount'])
    await drain(container)

    # Крутим очередь ещё несколько раз и дожимаем вручную.
    await drain(container, passes=8)
    await api.post(f'/admin/orders/{order["id"]}/retry-delivery')
    await api.post('/admin/retry-stuck')
    await drain(container, passes=8)

    final = await get_order(api, order['id'])
    assert sorted(item_statuses(final)) == ['delivered', 'refunded']
    assert payments.refund_count() - refunds_before == 1

    paid, delivered, refunded = await money(config, order['id'])
    assert paid == delivered + refunded

    maker = new_session_maker(config.postgres)
    async with maker() as session:
        deliveries = (
            await session.execute(
                text(
                    'SELECT count(*) FROM deliveries d '
                    'JOIN order_items i ON i.id = d.order_item_id '
                    'WHERE i.order_id = :id'
                ),
                {'id': order['id']},
            )
        ).scalar_one()
        refund_entries = (
            await session.execute(
                text(
                    "SELECT count(*) FROM ledger_entries "
                    "WHERE order_id = :id AND ref_type = 'refund'"
                ),
                {'id': order['id']},
            )
        ).scalar_one()
    await maker.kw['bind'].dispose()

    assert deliveries == 1, 'лишняя выдача'
    # Возврат — две проводки по две строки: обязательство и уход денег.
    assert refund_entries == 4, 'лишний возврат'


async def test_order_reaches_final_state_after_crash_mid_delivery(
    silent_api, silent_container, config, seeded
):
    """Процесс умер посреди выдачи: заказ всё равно доходит до конца.

    Побудка релея отключена, часть команд остаётся невыполненной — заказ
    закрывает плановый проход, а не удачное стечение обстоятельств.
    """
    order = await create_multi_order(
        silent_api, ('STEAM-TOPUP-500', 1), ('SUB-DISCORD-1M', 2)
    )
    await webhook(silent_api, order['id'], amount=order['total_amount'])

    # Оплата прошла, выдачи нет: всё, что осталось, — записи в аутбоксе.
    assert (await get_order(silent_api, order['id']))['status'] == 'paid'

    await drain(silent_container, passes=12)

    final = await get_order(silent_api, order['id'])
    assert final['status'] == 'partially_delivered'
    assert final['settled_at'] is not None

    paid, delivered, refunded = await money(config, order['id'])
    assert paid == delivered + refunded


async def test_money_always_reconciles_in_the_report(api, container, config, seeded):
    """Отчёт сверки не находит расхождений по деньгам."""
    for lines in (
        (('STEAM-TOPUP-500', 2),),
        (('SUB-DISCORD-1M', 2), ('KEY-GTA5', 1)),
        (('KEY-CS2-PRIME', 1), ('STEAM-TOPUP-500', 1)),
    ):
        order = await create_multi_order(api, *lines)
        await webhook(api, order['id'], amount=order['total_amount'])
    await drain(container, passes=12)

    report = await reconciliation(api)
    assert report['money_mismatch'] == []
    assert report['ledger_is_balanced']
    assert sum(b['balance'] for b in report['ledger_balances']) == 0
    assert report['stock_drift'] == []
