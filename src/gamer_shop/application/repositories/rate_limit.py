from typing import Protocol

from gamer_shop.application.dto import RateLimitDecisionDTO, RateLimitStateDTO
from gamer_shop.application.enums import SupplierName


class SupplierRateLimitRepository(Protocol):
    """Лимит поставщика: разрешения на обращения к нему.

    Живёт в базе, а не в памяти процесса: лимит у поставщика один на всех,
    а воркеров несколько. Одно разрешение — одно обращение; выдача разрешения
    умещается в один UPDATE, поэтому два воркера не выдадут его дважды.
    """

    async def acquire(self, supplier: SupplierName) -> RateLimitDecisionDTO:
        """Взять разрешение на одно обращение.

        Не выданное разрешение — не ошибка: очередь просто подождёт. Отказ
        несёт срок, через который появится следующее.
        """
        ...

    async def available(self) -> dict[str, float]:
        """Сколько разрешений можно выдать прямо сейчас, по поставщикам.

        Ничего не тратит: релей смотрит сюда, чтобы не брать в работу больше
        команд, чем сможет выполнить.
        """
        ...

    async def state(self) -> list[RateLimitStateDTO]: ...
