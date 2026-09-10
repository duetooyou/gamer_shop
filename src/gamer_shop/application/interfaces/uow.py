from typing import Protocol, Self


class UoW(Protocol):
    """Коммитит на успешном выходе, откатывает на исключении.

    Блоки вкладываются: фиксирует только внешний, внутренний ограничивается
    flush. Без этого интерактор, вызванный из другого интерактора, закоммитил
    бы половину работы — а команда аутбокса обязана лечь в ту же транзакцию,
    что и изменение состояния, которое её породило.
    """

    @property
    def in_transaction(self) -> bool: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def flush(self) -> None: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
