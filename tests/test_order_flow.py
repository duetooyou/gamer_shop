"""Создание заказа, получение по id, автовыдача после оплаты."""

from tests.helpers import (
    code_of,
    create_multi_order,
    create_order,
    first_item,
    get_order,
    item_statuses,
    webhook,
)


async def test_create_order_fixes_price_from_catalog(api, seeded):
    order = await create_order(api, 'STEAM-TOPUP-500')

    assert order['id'].startswith('ord_')
    assert order['status'] == 'created'
    # Цена из каталога, а не от клиента: с ней потом сверяется вебхук.
    assert order['total_amount'] == 500
    assert order['currency'] == 'RUB'

    item = first_item(order)
    assert item['id'] == f'{order["id"]}-1'
    assert item['price'] == 500
    assert item['status'] == 'pending'
    assert item['code'] is None


async def test_order_of_several_products_splits_by_supplier(api, seeded):
    """Каждая позиция уходит своему поставщику."""
    order = await create_multi_order(api, ('STEAM-TOPUP-500', 1), ('KEY-CS2-PRIME', 2))

    assert order['total_amount'] == 500 + 1290 * 2
    # Количество развёрнуто в отдельные позиции: у каждой свой код.
    assert [i['sku'] for i in order['items']] == [
        'STEAM-TOPUP-500', 'KEY-CS2-PRIME', 'KEY-CS2-PRIME'
    ]
    assert [i['position'] for i in order['items']] == [1, 2, 3]
    assert [i['supplier'] for i in order['items']] == ['a', 'b', 'b']


async def test_create_order_unknown_sku_returns_404(api, seeded):
    response = await api.post('/orders', json={'sku': 'NO-SUCH-SKU'})

    assert response.status_code == 404
    assert response.json()['error_code'] == 'CATALOG_001'


async def test_create_order_without_sku_and_items_is_rejected(api, seeded):
    response = await api.post('/orders', json={})

    assert response.status_code == 400


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
    assert final['paid_at'] is not None
    assert final['settled_at'] is not None

    item = first_item(final)
    assert item['status'] == 'delivered'
    assert item['delivered_by'] == 'a'
    assert item['delivered_at'] is not None
    # Код пришёл от поставщика, а не выдуман приложением.
    assert item['code'] in supplier_a.stats()['issued'].values()


async def test_multi_item_order_is_delivered_whole(api, seeded, suppliers, container):
    """Все позиции выданы — заказ закрывается как delivered."""
    supplier_a, supplier_b = suppliers
    order = await create_multi_order(api, ('STEAM-TOPUP-500', 1), ('KEY-CS2-PRIME', 1))

    await webhook(api, order['id'], amount=500 + 1290)

    final = await get_order(api, order['id'])
    assert final['status'] == 'delivered'
    assert item_statuses(final) == ['delivered', 'delivered']

    codes = {code_of(final, 1), code_of(final, 2)}
    assert len(codes) == 2, 'один код не может уйти в две позиции'
    assert code_of(final, 1) in supplier_a.stats()['issued'].values()
    assert code_of(final, 2) in supplier_b.stats()['issued'].values()


async def test_failed_payment_moves_to_payment_failed(api, seeded):
    order = await create_order(api, 'KEY-GTA5')

    ack = await webhook(api, order['id'], amount=1990, status='failed')
    assert ack['outcome'] == 'applied'

    final = await get_order(api, order['id'])
    assert final['status'] == 'payment_failed'
    assert first_item(final)['status'] == 'pending'
    assert first_item(final)['code'] is None
