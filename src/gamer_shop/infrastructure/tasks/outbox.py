"""Задачи релея.

Плановый проход — сетка безопасности, обычный путь — побудка сразу после
постановки команды. Гарантия доставки при этом ни от того, ни от другого не
зависит: она в таблице.
"""

import asyncio
from datetime import timedelta

from dishka.integrations.taskiq import FromDishka, inject

from gamer_shop.application.interactors import (
    OutboxRelayInteractor,
    PruneOutboxInteractor,
)
from gamer_shop.infrastructure.config import Config
from gamer_shop.infrastructure.taskiq_base.broker import broker

_config = Config()
_SCAN_INTERVAL = timedelta(seconds=_config.delivery.scan_interval_seconds)
_PRUNE_INTERVAL = timedelta(seconds=_config.outbox.keep_done_seconds)


@broker.task(task_name='outbox_relay', schedule=[{'interval': _SCAN_INTERVAL}])
@inject(patch_module=True)
async def outbox_relay_task(interactor: FromDishka[OutboxRelayInteractor]) -> int:
    """Проход по очереди команд.

    Полная пачка означает, что работа осталась, — зовём себя снова, чтобы
    всплеск разошёлся сразу, а не по одной пачке за плановый тик.

    Второй повод позвать себя — упёршийся в лимит поставщик. Работа есть,
    брать её нельзя, и ждать планового тика незачем: токен появится через
    секунду. Здесь и только здесь короткий сон уместен — иначе повторный
    заход выродится в busy loop.
    """
    result = await interactor.run_pass()
    if result.taken >= interactor.batch_size:
        await outbox_relay_task.kiq()
    elif result.more:
        await asyncio.sleep(result.retry_after_seconds)
        await outbox_relay_task.kiq()
    return result.taken


@broker.task(task_name='outbox_prune', schedule=[{'interval': _PRUNE_INTERVAL}])
@inject(patch_module=True)
async def outbox_prune_task(interactor: FromDishka[PruneOutboxInteractor]) -> int:
    return await interactor.execute()
