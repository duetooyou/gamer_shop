from typing import Protocol

from gamer_shop.application.dto import NewCommandDTO, OutboxCommandDTO, OutboxStatsDTO


class OutboxRepository(Protocol):
    """Очередь команд, живущая в той же базе, что и состояние.

    Взятие в работу — не блокировка строки, а аренда: команда прячется на
    lease_seconds и всплывает сама, если исполнитель умер, не отчитавшись.
    Поэтому потерять команду нельзя, а выполниться она может дважды —
    идемпотентность обеспечивают сами обработчики.
    """

    async def enqueue(self, command: NewCommandDTO) -> bool: ...

    async def claim(
        self, limit: int, lease_seconds: int, partition_key: str | None = None
    ) -> list[OutboxCommandDTO]: ...

    async def ready_by_partition(self) -> dict[str, int]:
        """Сколько команд каждой партиции доступно прямо сейчас."""
        ...

    async def mark_done(self, command_id: int) -> None: ...

    async def postpone(self, command_id: int, delay_seconds: float) -> None:
        """Отложить, не считая попыткой: работа не начиналась."""
        ...

    async def reschedule(self, command_id: int, delay_seconds: float, error: str) -> None: ...

    async def mark_dead(self, command_id: int, error: str) -> None: ...

    async def stats(self) -> list[OutboxStatsDTO]: ...

    async def dead(self, limit: int) -> list[OutboxCommandDTO]: ...

    async def prune(self, older_than_seconds: int) -> int: ...
