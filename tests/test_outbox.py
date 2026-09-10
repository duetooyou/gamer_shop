"""Очередь команд.

Аутбокс обязан отвечать за две вещи: команда не теряется и не удваивается.
Здесь проверяется и то и другое, включая смерть исполнителя посреди работы.
"""

import asyncio

import pytest

from gamer_shop.application.dto import NewCommandDTO
from gamer_shop.application.enums import (
    PRIORITY_NORMAL,
    PRIORITY_PAID,
    OutboxCommandKind,
)
from gamer_shop.application.interfaces import UoW
from gamer_shop.application.repositories import OutboxRepository


def command(dedup_key: str, priority: int = PRIORITY_NORMAL) -> NewCommandDTO:
    return NewCommandDTO(
        kind=OutboxCommandKind.DELIVER_ITEM,
        dedup_key=dedup_key,
        payload={'order_item_id': dedup_key},
        priority=priority,
    )


async def test_enqueue_requires_transaction(container):
    """Постановка вне транзакции запрещена: команда разъедется с состоянием."""
    async with container() as scope:
        outbox = await scope.get(OutboxRepository)

        with pytest.raises(RuntimeError, match='вне транзакции'):
            await outbox.enqueue(command('ord_00001'))


async def test_duplicate_pending_command_is_not_created(container):
    async with container() as scope:
        uow = await scope.get(UoW)
        outbox = await scope.get(OutboxRepository)

        async with uow:
            assert await outbox.enqueue(command('ord_00001')) is True
        async with uow:
            assert await outbox.enqueue(command('ord_00001')) is False

        claimed = await outbox.claim(limit=10, lease_seconds=60)
        assert len(claimed) == 1


async def test_done_command_frees_the_dedup_key(container):
    """Выполненная команда не мешает поставить такую же заново —
    иначе повторную выдачу по заказу было бы не запросить."""
    async with container() as scope:
        uow = await scope.get(UoW)
        outbox = await scope.get(OutboxRepository)

        async with uow:
            await outbox.enqueue(command('ord_00001'))
        claimed = await outbox.claim(limit=10, lease_seconds=60)
        async with uow:
            await outbox.mark_done(claimed[0].id)

        async with uow:
            assert await outbox.enqueue(command('ord_00001')) is True


async def test_claim_hides_command_under_lease(container):
    """Взятая в работу команда не видна второму исполнителю."""
    async with container() as scope:
        uow = await scope.get(UoW)
        outbox = await scope.get(OutboxRepository)

        async with uow:
            await outbox.enqueue(command('ord_00001'))

        first = await outbox.claim(limit=10, lease_seconds=60)
        second = await outbox.claim(limit=10, lease_seconds=60)

        assert len(first) == 1
        assert second == []
        assert first[0].attempts == 1


async def test_expired_lease_returns_command_to_queue(container):
    """Исполнитель умер, не отчитавшись, — команда всплывает сама."""
    async with container() as scope:
        uow = await scope.get(UoW)
        outbox = await scope.get(OutboxRepository)

        async with uow:
            await outbox.enqueue(command('ord_00001'))

        taken = await outbox.claim(limit=10, lease_seconds=1)
        assert len(taken) == 1

        await asyncio.sleep(1.2)
        again = await outbox.claim(limit=10, lease_seconds=60)

        assert len(again) == 1
        # Тот же предмет, вторая выдача в работу.
        assert again[0].dedup_key == taken[0].dedup_key
        assert again[0].attempts == 2


async def test_paid_orders_are_claimed_first(container):
    """Пункт 3 задачи 3: оплаченные обслуживаются раньше неоплаченных."""
    async with container() as scope:
        uow = await scope.get(UoW)
        outbox = await scope.get(OutboxRepository)

        async with uow:
            await outbox.enqueue(command('ord_00001', PRIORITY_NORMAL))
            await outbox.enqueue(command('ord_00002', PRIORITY_NORMAL))
            # Встал в очередь последним, но оплачен.
            await outbox.enqueue(command('ord_00003', PRIORITY_PAID))

        claimed = await outbox.claim(limit=1, lease_seconds=60)

        assert [c.dedup_key for c in claimed] == ['ord_00003']


async def test_reschedule_delays_without_losing_command(container):
    async with container() as scope:
        uow = await scope.get(UoW)
        outbox = await scope.get(OutboxRepository)

        async with uow:
            await outbox.enqueue(command('ord_00001'))
        claimed = await outbox.claim(limit=10, lease_seconds=60)

        async with uow:
            await outbox.reschedule(claimed[0].id, delay_seconds=1, error='поставщик недоступен')

        assert await outbox.claim(limit=10, lease_seconds=60) == []
        await asyncio.sleep(1.2)
        assert len(await outbox.claim(limit=10, lease_seconds=60)) == 1


async def test_dead_commands_are_visible(container):
    """Исчерпавшая попытки команда не исчезает, а показывается в сверке."""
    async with container() as scope:
        uow = await scope.get(UoW)
        outbox = await scope.get(OutboxRepository)

        async with uow:
            await outbox.enqueue(command('ord_00001'))
        claimed = await outbox.claim(limit=10, lease_seconds=60)
        async with uow:
            await outbox.mark_dead(claimed[0].id, 'попытки исчерпаны')

        dead = await outbox.dead(limit=10)
        assert [c.dedup_key for c in dead] == ['ord_00001']
        assert await outbox.claim(limit=10, lease_seconds=60) == []
