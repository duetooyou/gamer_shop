"""Восстановление картины на любой прошлый момент.

Состояние заказа хранится перезаписью, поэтому «как было» берётся не из
таблиц заказов, а из двух журналов, которые только дополняются: журнала
состояний и журнала денежных движений. Здесь проверяется, что журналов
достаточно, что переписать их нельзя и что итоги за период из них сходятся.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from gamer_shop.infrastructure.database import new_session_maker
from tests.helpers import (
    create_multi_order,
    drain_until_quiet,
    get_order,
    period_totals,
    state_at,
    state_at_response,
    webhook,
)


async def db_now(config) -> datetime:
    """Момент по часам базы: журналы отмечены ими же."""
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        moment = (await session.execute(text('SELECT now()'))).scalar_one()
    await maker.kw['bind'].dispose()
    return moment


async def run_sql(config, sql: str) -> None:
    maker = new_session_maker(config.postgres)
    try:
        async with maker() as session:
            await session.execute(text(sql))
            await session.commit()
    finally:
        await maker.kw['bind'].dispose()


async def partial_order(silent_api, silent_container, config, payments):
    """Заказ, где одна позиция выдаётся, а за вторую возвращаются деньги.

    Возвращает моменты до заказа, после создания, после оплаты и после
    расчёта — их и предстоит восстанавливать.
    """
    before = await db_now(config)
    # У SUB-DISCORD-1M остаток равен единице, а берём две.
    order = await create_multi_order(
        silent_api, ('STEAM-TOPUP-500', 1), ('SUB-DISCORD-1M', 2)
    )
    created = await db_now(config)

    await webhook(silent_api, order['id'], amount=order['total_amount'])
    paid = await db_now(config)

    await drain_until_quiet(silent_container)
    settled = await db_now(config)
    return order, before, created, paid, settled


async def test_state_at_past_moment_is_what_it_was(
    silent_api, silent_container, config, seeded, payments
):
    """Четыре среза одного заказа: до него, после создания, после оплаты,
    после расчёта. Каждый показывает то, что было тогда, а не сейчас."""
    order, before, created, paid, settled = await partial_order(
        silent_api, silent_container, config, payments
    )
    total = order['total_amount']

    was_nothing = await state_at(silent_api, order['id'], before)
    assert was_nothing['exists'] is False

    was_created = await state_at(silent_api, order['id'], created)
    assert was_created['status'] == 'created'
    assert was_created['total_amount'] == total
    assert [i['status'] for i in was_created['items']] == ['pending'] * 3
    assert was_created['money'] == {
        'paid': 0, 'delivered': 0, 'refunded': 0, 'outstanding': 0
    }

    was_paid = await state_at(silent_api, order['id'], paid)
    assert was_paid['status'] == 'paid'
    assert [i['status'] for i in was_paid['items']] == ['paid'] * 3
    # Деньги пришли, но пока никому не принадлежат: обязательство висит.
    assert was_paid['money'] == {
        'paid': total, 'delivered': 0, 'refunded': 0, 'outstanding': total
    }
    assert not any(i['code_issued'] for i in was_paid['items'])

    was_settled = await state_at(silent_api, order['id'], settled)
    assert was_settled['status'] == 'partially_delivered'
    assert sorted(i['status'] for i in was_settled['items']) == [
        'delivered', 'delivered', 'refunded'
    ]
    money = was_settled['money']
    assert money['paid'] == money['delivered'] + money['refunded']
    assert money['outstanding'] == 0

    # Срез без as_of — это «сейчас», и он совпадает с последним.
    assert (await state_at(silent_api, order['id']))['status'] == was_settled['status']
    # И с тем, что отдаёт обычная витрина заказа.
    assert (await get_order(silent_api, order['id']))['status'] == was_settled['status']


async def test_events_only_grow_and_keep_their_order(
    silent_api, silent_container, config, seeded, payments
):
    """Журнал дописывается: срез на поздний момент включает ранний целиком."""
    order, _, created, _, settled = await partial_order(
        silent_api, silent_container, config, payments
    )

    early = await state_at(silent_api, order['id'], created, with_events=True)
    late = await state_at(silent_api, order['id'], settled, with_events=True)

    early_ids = [e['id'] for e in early['events']]
    late_ids = [e['id'] for e in late['events']]
    assert early_ids, 'создание заказа обязано оставить след'
    assert late_ids[: len(early_ids)] == early_ids
    assert len(late_ids) > len(early_ids)
    assert late_ids == sorted(late_ids)

    kinds = [e['kind'] for e in late['events']]
    assert kinds[0] == 'order_created'
    assert 'item_created' in kinds
    assert 'code_issued' in kinds
    assert 'order_status_changed' in kinds


@pytest.mark.parametrize(
    'sql',
    [
        "UPDATE order_events SET to_status = 'delivered'",
        'DELETE FROM order_events',
        'UPDATE ledger_entries SET amount = 1',
        'DELETE FROM ledger_entries',
    ],
)
async def test_history_cannot_be_rewritten(
    silent_api, silent_container, config, seeded, payments, sql
):
    """Задним числом ничего не переписывается — и это ограничение базы,
    а не обещание приложения."""
    await partial_order(silent_api, silent_container, config, payments)

    with pytest.raises(DBAPIError, match='только дополняется'):
        await run_sql(config, sql)


async def test_period_totals_add_up(
    silent_api, silent_container, config, seeded, payments
):
    """Оплачено за период равно выданному, возвращённому и приросту
    обязательства перед покупателем."""
    order, before, _, _, settled = await partial_order(
        silent_api, silent_container, config, payments
    )

    totals = await period_totals(silent_api, before, settled)
    assert totals['balanced'] is True
    assert totals['paid'] == order['total_amount']
    assert totals['paid'] == (
        totals['delivered'] + totals['refunded'] + totals['outstanding_change']
    )
    assert totals['orders_created'] == 1
    assert totals['orders_settled'] == 1
    assert totals['items_delivered'] == 2
    assert totals['items_refunded'] == 1


async def test_adjacent_periods_sum_to_the_whole(
    silent_api, silent_container, config, seeded, payments
):
    """Период, разрезанный надвое, даёт ту же сумму.

    Проверка не арифметики, а границ: проводка не может попасть в оба
    куска или не попасть ни в один.
    """
    order, before, _, paid, settled = await partial_order(
        silent_api, silent_container, config, payments
    )

    whole = await period_totals(silent_api, before, settled)
    first = await period_totals(silent_api, before, paid)
    second = await period_totals(silent_api, paid, settled)

    for key in ('paid', 'delivered', 'refunded', 'items_delivered', 'items_refunded'):
        assert first[key] + second[key] == whole[key], key

    # Обязательство на стыке — одно и то же число с обеих сторон.
    assert first['closing_outstanding'] == second['opening_outstanding']
    assert first['balanced'] and second['balanced']


async def test_moment_before_everything_is_empty(silent_api, config, seeded):
    """Срез на время, когда ничего не происходило, пуст, а не сломан."""
    long_ago = (await db_now(config)) - timedelta(days=365)
    totals = await period_totals(silent_api, long_ago, long_ago)
    assert totals['paid'] == 0
    assert totals['orders_created'] == 0
    assert totals['balanced'] is True


async def test_date_is_as_good_a_question_as_a_moment(
    silent_api, silent_container, config, seeded, payments
):
    """«Что было десятого сентября» — законный вопрос, и дата это сутки.

    Срез на дату берётся с конца суток, поэтому в него попадает весь день,
    а не его первая миллисекунда.
    """
    order, before, _, _, settled = await partial_order(
        silent_api, silent_container, config, payments
    )

    today = settled.date().isoformat()
    yesterday = (settled.date() - timedelta(days=1)).isoformat()

    by_date = await state_at(silent_api, order['id'], today)
    by_moment = await state_at(silent_api, order['id'], settled)
    assert by_date['status'] == by_moment['status'] == 'partially_delivered'
    assert by_date['money'] == by_moment['money']

    # Накануне заказа ещё не было.
    assert (await state_at(silent_api, order['id'], yesterday))['exists'] is False

    # Границы периода по датам: начало суток и их конец.
    totals = await period_totals(silent_api, today, today)
    assert totals['period_from'].startswith(f'{today}T00:00:00')
    assert totals['period_to'].startswith(f'{today}T23:59:59')
    assert totals['paid'] >= order['total_amount']
    assert totals['balanced'] is True


async def test_unparsable_moment_says_what_it_wanted(silent_api, seeded):
    """Негодная метка — понятный отказ, а не «Invalid RFC3339»."""
    response = await state_at_response(silent_api, 'ord_00001', 'вчера')
    assert response.status_code == 400
    assert 'не разобрать метку времени' in response.text
    assert '2026-09-10' in response.text, 'в отказе должен быть пример'
