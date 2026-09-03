"""Создание заказа, получение по id, автовыдача после оплаты."""

import pytest

from tests.helpers import create_order, get_order, webhook



async def test_create_order_fixes_price_from_catalog(api, seeded):
    order = await create_order(api, 'STEAM-TOPUP-500')

    assert order['id'].startswith('ord_')
    assert order['status'] == 'created'
    # Цена из каталога, а не от клиента: с ней потом сверяется вебхук.
    assert order['price'] == 500
    assert order['currency'] == 'RUB'
    assert order['code'] is None


async def test_create_order_unknown_sku_returns_404(api, seeded):
    response = await api.post('/orders', json={'sku': 'NO-SUCH-SKU'})

    assert response.status_code == 404
    assert response.json()['error_code'] == 'CATALOG_001'


async def test_get_unknown_order_returns_404(api, seeded):
    response = await api.get('/orders/ord_00000')

    assert response.status_code == 404
    assert response.json()['error_code'] == 'ORDER_001'


async def test_happy_path_created_paid_delivering_delivered(api, seeded, suppliers):
    supplier_a, _ = suppliers
    order = await create_order(api, 'STEAM-TOPUP-500')

    ack = await webhook(api, order['id'], amount=500)
    assert ack['outcome'] == 'applied'

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert final['code']
    assert final['supplier'] == 'a'
    assert final['paid_at'] is not None
    assert final['delivered_at'] is not None
    # Код пришёл от поставщика, а не выдуман приложением.
    assert final['code'] in supplier_a.stats()['issued'].values()


async def test_failed_payment_moves_to_payment_failed(api, seeded):
    order = await create_order(api, 'KEY-GTA5')

    ack = await webhook(api, order['id'], amount=1990, status='failed')
    assert ack['outcome'] == 'applied'

    final = await get_order(api, order['id'])
    assert final['status'] == 'payment_failed'
    assert final['code'] is None
