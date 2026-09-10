"""Всплеск заказов и лимит поставщика.

У заглушки собственное ведро токенов — такое же, как у настоящего
поставщика. Магазин держит своё: берёт токен до запроса и идёт заведомо
медленнее объявленного лимита. Запас обязателен, а не перестраховочен —
своё ведро магазин опустошает раньше чужого ровно на сетевую задержку, и на
одинаковых числах оба идут ноздря в ноздрю.

Отсюда проверка: счётчик over_limit заглушки остаётся нулём. Чтобы он не
оказался нулём просто потому, что заглушка не умеет считать, соседний тест
снимает учёт с магазина и получает превышение немедленно.
"""

import asyncio

from tests.helpers import (
    create_order,
    drain_until_quiet,
    get_order,
    progress,
    reconciliation,
    webhook,
)

# Десять обращений в секунду при всплеске в шесть: заметно быстрее, чем
# заказы приходят, и достаточно медленно, чтобы очередь была видна.
LIMIT_PER_MINUTE = 600
SUPPLIER_BURST = 6
# Всплеск магазина строго меньше того, что стерпит поставщик.
BURST = 3

# Меньше остатка по SKU: этот тест про лимит, а не про пустой склад.
ORDERS = 8


def set_limits(config, suppliers, burst: int = BURST) -> None:
    for stub in suppliers:
        stub.set_rate_limit(LIMIT_PER_MINUTE, SUPPLIER_BURST)
    config.supplier.rate_limit_per_minute = LIMIT_PER_MINUTE
    config.supplier.rate_limit_burst = burst


async def pay_burst(api, count: int = ORDERS) -> list[dict]:
    """Пачка оплаченных заказов разом — тот самый всплеск."""
    orders = [await create_order(api, 'KEY-GTA5') for _ in range(count)]
    await asyncio.gather(
        *(webhook(api, order['id'], amount=order['total_amount']) for order in orders)
    )
    return orders


async def test_burst_never_exceeds_supplier_limit(
    api, container, config, seeded, suppliers
):
    """Заказов больше, чем поставщик готов принять, — а 429 ни одного."""
    set_limits(config, suppliers)
    orders = await pay_burst(api)

    await drain_until_quiet(container)

    for order in orders:
        assert (await get_order(api, order['id']))['status'] == 'delivered'

    a, b = suppliers
    assert a.stats()['over_limit'] == 0
    assert b.stats()['over_limit'] == 0
    # Очередь упиралась в лимит — иначе тест ничего не проверил бы.
    limits = {r['supplier']: r for r in (await progress(api))['rate_limits']}
    assert limits['a']['throttled'] > 0


async def test_without_the_bucket_the_supplier_limit_is_broken(
    api, container, config, seeded, suppliers
):
    """Обратная проверка: без учёта превышение случается сразу.

    Иначе первый тест доказывал бы лишь то, что заглушка не умеет считать.
    """
    set_limits(config, suppliers, burst=10_000)
    await pay_burst(api)

    await drain_until_quiet(container)

    a, _ = suppliers
    assert a.stats()['over_limit'] > 0


async def test_nothing_is_lost_while_waiting_for_the_limit(
    silent_api, silent_container, config, seeded, suppliers
):
    """Заказы, не влезшие в лимит, стоят в очереди, а не пропадают.

    Побудка релея здесь не работает: очередь копится целиком, и первый
    проход видно во всей полноте — сколько взял и сколько осталось.
    """
    set_limits(config, suppliers)
    orders = await pay_burst(silent_api)

    before = await progress(silent_api)
    assert before['items_in_queue'] == len(orders)
    assert sum(q['queued'] for q in before['queue']) == len(orders)

    # Один проход релея: больше, чем есть токенов, он не возьмёт.
    from gamer_shop.application.interactors import OutboxRelayInteractor

    async with silent_container() as scope:
        first = await (await scope.get(OutboxRelayInteractor)).run_pass()
    assert first.taken <= BURST
    assert first.more, 'работа осталась, и релей обязан это видеть'

    await drain_until_quiet(silent_container)

    done = await progress(silent_api)
    assert done['items_delivered'] == len(orders)
    assert done['items_in_queue'] == 0
    assert done['dead_commands'] == 0
    assert (await reconciliation(silent_api))['ledger_is_balanced']


async def test_progress_shows_queue_and_delivered(
    api, container, config, seeded, suppliers
):
    """Видно, сколько в очереди и сколько уже выдано."""
    set_limits(config, suppliers)
    empty = await progress(api)
    assert empty['items_delivered'] == 0
    assert empty['items_in_queue'] == 0

    orders = await pay_burst(api, count=6)
    await drain_until_quiet(container)

    done = await progress(api)
    assert done['orders_total'] == len(orders)
    assert done['orders_settled'] == len(orders)
    assert done['items_delivered'] == len(orders)
    assert done['items_refunded'] == 0
    rates = {r['supplier']: r for r in done['rate_limits']}
    assert rates['a']['granted'] >= len(orders)
    # Идём ниже объявленного лимита — в этом и смысл запаса.
    assert rates['a']['pace_per_minute'] < LIMIT_PER_MINUTE


async def test_paid_orders_are_served_before_unpaid(
    api, container, config, seeded, suppliers
):
    """Неоплаченный заказ не занимает место в очереди к поставщику.

    Приоритет здесь не смягчающее правило, а свойство построения: команда
    выдачи ставится в той же транзакции, что и подтверждение оплаты, и до
    неё неоплаченного заказа в очереди попросту нет. Внутри очереди
    оплаченное всё равно идёт первым — по priority DESC.
    """
    set_limits(config, suppliers)

    unpaid = await create_order(api, 'KEY-GTA5')  # создан раньше
    paid = await create_order(api, 'KEY-GTA5')
    await webhook(api, paid['id'], amount=paid['total_amount'])

    await drain_until_quiet(container)

    assert (await get_order(api, paid['id']))['status'] == 'delivered'
    assert (await get_order(api, unpaid['id']))['status'] == 'created'

    # Позиция неоплаченного заказа к поставщику не уходила.
    a, b = suppliers
    issued = {**a.stats()['issued'], **b.stats()['issued']}
    assert not any(f'{unpaid["id"]}-' in request_id for request_id in issued)
