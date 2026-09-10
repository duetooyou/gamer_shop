"""Релей аутбокса.

Команду нельзя потерять: она возвращается в очередь по истечении аренды,
даже если процесс умер молча. Обратная сторона — команда может выполниться
дважды, поэтому каждый обработчик обязан быть идемпотентным.

Здесь же живёт единственное место, где всплеск заказов встречается с лимитом
поставщика. Релей не берёт из очереди больше, чем сможет выполнить: сколько
у поставщика осталось разрешений, столько команд к нему и уедет, остальные
дождутся своей очереди нетронутыми.
"""

from gamer_shop.application.dto import (
    OutboxCommandDTO,
    OutboxStatsDTO,
    RelayPassDTO,
)
from gamer_shop.application.enums import SupplierName
from gamer_shop.application.exceptions import SupplierThrottledException
from gamer_shop.application.interfaces import (
    CommandDispatcher,
    DeliveryLogger,
    UoW,
)
from gamer_shop.application.policies import backoff_delay
from gamer_shop.application.repositories import (
    OutboxRepository,
    SupplierRateLimitRepository,
)

# Партиции, у которых есть внешний лимит. Остальные — расчёты и возвраты —
# упираются только в собственную скорость.
_LIMITED_PARTITIONS = frozenset(supplier.value for supplier in SupplierName)


class OutboxRelayInteractor:
    def __init__(
        self,
        uow: UoW,
        outbox: OutboxRepository,
        limits: SupplierRateLimitRepository,
        dispatcher: CommandDispatcher,
        logger: DeliveryLogger,
        batch_size: int,
        lease_seconds: int,
        max_attempts: int,
        backoff_base: float,
        backoff_max: float,
        poll_interval_seconds: float,
    ) -> None:
        self._uow = uow
        self._outbox = outbox
        self._limits = limits
        self._dispatcher = dispatcher
        self._logger = logger
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._poll_interval_seconds = poll_interval_seconds

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def run_once(self) -> int:
        """Один проход. Возвращает число взятых команд — полная пачка
        означает, что работа осталась и звать стоит сразу же."""
        return (await self.run_pass()).taken

    async def run_pass(self) -> RelayPassDTO:
        """Проход с полным итогом: сколько взято и что осталось за лимитом."""
        async with self._uow:
            ready = await self._outbox.ready_by_partition()
            budget = await self._limits.available()
        if not ready:
            return RelayPassDTO(taken=0)

        commands: list[OutboxCommandDTO] = []
        throttled: list[str] = []
        remaining = self._batch_size
        # Доля на партицию: очередь к одному поставщику не должна съедать
        # пачку целиком, иначе расчёты и возвраты будут ждать выдач.
        share = max(1, self._batch_size // len(ready))

        for partition, waiting in sorted(ready.items()):
            if remaining <= 0:
                break
            take = min(share, remaining, waiting)
            if partition in _LIMITED_PARTITIONS:
                allowed = int(budget.get(partition, take))
                if allowed < take:
                    # Работы больше, чем разрешений: остаток ждёт в очереди,
                    # и звать себя снова придётся раньше планового тика.
                    throttled.append(partition)
                    take = allowed
                if take < 1:
                    continue

            async with self._uow:
                claimed = await self._outbox.claim(take, self._lease_seconds, partition)
            commands.extend(claimed)
            remaining -= len(claimed)

        for command in commands:
            await self._run(command)

        if throttled:
            self._logger.info(
                'outbox_partitions_throttled',
                partitions=throttled,
                waiting=sum(ready[p] for p in throttled),
            )
        return RelayPassDTO(
            taken=len(commands),
            throttled=tuple(throttled),
            retry_after_seconds=self._poll_interval_seconds if throttled else 0.0,
        )

    async def stats(self) -> list[OutboxStatsDTO]:
        return await self._outbox.stats()

    async def _run(self, command: OutboxCommandDTO) -> None:
        log = self._logger.bind(
            command_id=command.id,
            kind=command.kind,
            dedup_key=command.dedup_key,
            attempts=command.attempts,
        )
        try:
            await self._dispatcher.dispatch(command)
        except Exception as exc:  # noqa: BLE001 — исход решает релей, не обработчик
            await self._on_failure(command, exc, log)
            return

        async with self._uow:
            await self._outbox.mark_done(command.id)
        log.info('outbox_command_done')

    async def _on_failure(self, command: OutboxCommandDTO, exc: Exception, log) -> None:
        if isinstance(exc, SupplierThrottledException):
            # Разрешение перехватили между проверкой бюджета и обращением.
            # Работа не начиналась, попытку не засчитываем.
            async with self._uow:
                await self._outbox.postpone(command.id, exc.retry_after_seconds)
            log.info(
                'outbox_command_throttled',
                delay=round(exc.retry_after_seconds, 3),
                **exc.details,
            )
            return

        error = f'{type(exc).__name__}: {exc}'

        if command.attempts >= self._max_attempts:
            # Не удаляем: исчерпавшая попытки команда обязана быть видна
            # в сверке, иначе «ничего не теряется» превратится в слова.
            async with self._uow:
                await self._outbox.mark_dead(command.id, error)
            log.error('outbox_command_dead', error=error)
            return

        delay = backoff_delay(command.attempts, self._backoff_base, self._backoff_max, 0.1)
        async with self._uow:
            await self._outbox.reschedule(command.id, delay, error)
        log.warning('outbox_command_retry', error=error, delay=round(delay, 3))


class PruneOutboxInteractor:
    """Уборка выполненных команд. История живёт в отдельном журнале —
    здесь хранить её незачем."""

    def __init__(
        self, uow: UoW, outbox: OutboxRepository, logger: DeliveryLogger, keep_seconds: int
    ) -> None:
        self._uow = uow
        self._outbox = outbox
        self._logger = logger
        self._keep_seconds = keep_seconds

    async def execute(self) -> int:
        async with self._uow:
            removed = await self._outbox.prune(self._keep_seconds)
        if removed:
            self._logger.info('outbox_pruned', removed=removed)
        return removed
