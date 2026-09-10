"""Границы транзакции UoW.

Проверяется ровно то свойство, на которое опирается аутбокс: изменение
состояния и команда, которую оно порождает, коммитятся вместе или не
коммитятся вовсе.
"""

import pytest
from sqlalchemy import text

from gamer_shop.application.dto import NewOrderLine
from gamer_shop.application.enums import SupplierName
from gamer_shop.application.interfaces import UoW
from gamer_shop.application.repositories import OrderRepository
from gamer_shop.infrastructure.database import new_session_maker


def line(sku: str, price: int) -> NewOrderLine:
    return NewOrderLine(sku=sku, price=price, currency='RUB', supplier=SupplierName.A)


async def order_count(config) -> int:
    """Счётчик из отдельного соединения: чужая транзакция ему не видна."""
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        count = (await session.execute(text('SELECT count(*) FROM orders'))).scalar_one()
    await maker.kw['bind'].dispose()
    return int(count)


async def test_in_transaction_tracks_nesting(container):
    async with container() as scope:
        uow = await scope.get(UoW)

        assert not uow.in_transaction
        async with uow:
            assert uow.in_transaction
            async with uow:
                assert uow.in_transaction
            assert uow.in_transaction
        assert not uow.in_transaction


async def test_inner_block_does_not_commit(container, config, seeded):
    async with container() as scope:
        uow = await scope.get(UoW)
        orders = await scope.get(OrderRepository)

        async with uow:
            async with uow:
                await orders.create([line('KEY-GTA5', 1990)])
            # Внутренний блок вышел — снаружи заказа ещё нет.
            assert await order_count(config) == 0

    assert await order_count(config) == 1


async def test_inner_failure_rolls_back_outer(container, config, seeded):
    async with container() as scope:
        uow = await scope.get(UoW)
        orders = await scope.get(OrderRepository)

        async with uow:
            await orders.create([line('KEY-GTA5', 1990)])
            with pytest.raises(RuntimeError):
                async with uow:
                    await orders.create([line('KEY-CS2-PRIME', 1290)])
                    raise RuntimeError('сбой в середине')
            # Исключение поймано здесь, но внешний блок обязан откатиться.

    assert await order_count(config) == 0
