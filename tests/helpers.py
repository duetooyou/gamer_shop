"""Обёртки над HTTP-контрактами, чтобы тесты читались как сценарии."""

import asyncio
import time
from datetime import datetime
from uuid import uuid4

import httpx


async def create_order(api: httpx.AsyncClient, sku: str) -> dict:
    """Заказ из одной позиции — форма первого этапа."""
    response = await api.post('/orders', json={'sku': sku})
    response.raise_for_status()
    return response.json()


async def create_multi_order(api: httpx.AsyncClient, *lines: tuple[str, int]) -> dict:
    """Заказ из нескольких товаров: ('SKU', количество)."""
    response = await api.post(
        '/orders',
        json={'items': [{'sku': sku, 'quantity': qty} for sku, qty in lines]},
    )
    response.raise_for_status()
    return response.json()


async def get_order(api: httpx.AsyncClient, order_id: str) -> dict:
    response = await api.get(f'/orders/{order_id}')
    response.raise_for_status()
    return response.json()


def first_item(order: dict) -> dict:
    return order['items'][0]


def code_of(order: dict, position: int = 1) -> str | None:
    return order['items'][position - 1]['code']


def delivered_by(order: dict, position: int = 1) -> str | None:
    """Кто выдал фактически — после фолбэка это не назначенный поставщик."""
    return order['items'][position - 1]['delivered_by']


def item_statuses(order: dict) -> list[str]:
    return [i['status'] for i in order['items']]


def payload(
    order_id: str,
    amount: int,
    status: str = 'paid',
    currency: str = 'RUB',
    event_id: str | None = None,
) -> dict:
    return {
        'event_id': event_id or f'evt_{uuid4().hex[:12]}',
        'order_id': order_id,
        'status': status,
        'amount': amount,
        'currency': currency,
        'created_at': '2025-01-01T12:00:00Z',
    }


async def webhook(
    api: httpx.AsyncClient,
    order_id: str,
    amount: int,
    status: str = 'paid',
    currency: str = 'RUB',
    event_id: str | None = None,
) -> dict:
    response = await api.post(
        '/webhook/payment', json=payload(order_id, amount, status, currency, event_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def progress(api: httpx.AsyncClient) -> dict:
    response = await api.get('/admin/progress')
    response.raise_for_status()
    return response.json()


async def state_at_response(
    api: httpx.AsyncClient, order_id: str, as_of: datetime | str | None = None,
    with_events: bool = False,
) -> httpx.Response:
    """Срез заказа на момент, ответ как есть.

    Момент — datetime либо строка: ручка принимает и дату, и точное время,
    и отвечать на негодную метку тоже обязана внятно.
    """
    params: dict = {'with_events': str(with_events).lower()}
    if as_of is not None:
        params['as_of'] = as_of.isoformat() if isinstance(as_of, datetime) else as_of
    return await api.get(f'/admin/orders/{order_id}/at', params=params)


async def state_at(
    api: httpx.AsyncClient, order_id: str, as_of: datetime | str | None = None,
    with_events: bool = False,
) -> dict:
    response = await state_at_response(api, order_id, as_of, with_events)
    response.raise_for_status()
    return response.json()


async def period_totals(
    api: httpx.AsyncClient,
    period_from: datetime | str,
    period_to: datetime | str | None = None,
) -> dict:
    def moment(value: datetime | str) -> str:
        return value.isoformat() if isinstance(value, datetime) else value

    params = {'from': moment(period_from)}
    if period_to is not None:
        params['to'] = moment(period_to)
    response = await api.get('/admin/period-totals', params=params)
    response.raise_for_status()
    return response.json()


async def reconciliation(api: httpx.AsyncClient, older_than_seconds: int = 0) -> dict:
    response = await api.get(
        '/admin/reconciliation', params={'older_than_seconds': older_than_seconds}
    )
    response.raise_for_status()
    return response.json()


async def drain(container, passes: int = 6) -> None:
    """Прокрутить очередь команд до тишины.

    В тестах релей вызывается синхронно, но одна команда порождает другую
    (выдача — расчёт, отказ — возврат), поэтому проходов нужно несколько.
    """
    import asyncio

    from gamer_shop.application.interactors import OutboxRelayInteractor

    async with container() as scope:
        relay = await scope.get(OutboxRelayInteractor)
        for _ in range(passes):
            if await relay.run_once() == 0:
                # Команда могла уйти под бэкофф — дадим ей всплыть.
                await asyncio.sleep(0.12)
                if await relay.run_once() == 0:
                    return


async def drain_until_quiet(container, timeout_seconds: float = 30.0) -> None:
    """Крутить релей, пока очередь не разойдётся.

    Отличается от drain тем, что не принимает ноль взятых команд за конец
    работы: под лимитом поставщика проход возвращает ноль именно потому,
    что работа осталась.
    """
    from gamer_shop.application.interactors import OutboxRelayInteractor

    deadline = time.monotonic() + timeout_seconds
    async with container() as scope:
        relay = await scope.get(OutboxRelayInteractor)
        quiet = 0
        while time.monotonic() < deadline:
            result = await relay.run_pass()
            if result.taken == 0 and not result.more:
                # Команда могла уйти под бэкофф — дадим ей всплыть.
                quiet += 1
                if quiet >= 3:
                    return
                await asyncio.sleep(0.15)
                continue
            quiet = 0
            if result.more:
                await asyncio.sleep(result.retry_after_seconds)
    raise AssertionError('очередь не разошлась за отведённое время')
