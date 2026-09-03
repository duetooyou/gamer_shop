"""Обёртки над HTTP-контрактами, чтобы тесты читались как сценарии."""

from uuid import uuid4

import httpx


async def create_order(api: httpx.AsyncClient, sku: str) -> dict:
    response = await api.post('/orders', json={'sku': sku})
    response.raise_for_status()
    return response.json()


async def get_order(api: httpx.AsyncClient, order_id: str) -> dict:
    response = await api.get(f'/orders/{order_id}')
    response.raise_for_status()
    return response.json()


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


async def reconciliation(api: httpx.AsyncClient, older_than_seconds: int = 0) -> dict:
    response = await api.get(
        '/admin/reconciliation', params={'older_than_seconds': older_than_seconds}
    )
    response.raise_for_status()
    return response.json()
