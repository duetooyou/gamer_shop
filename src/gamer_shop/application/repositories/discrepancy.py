from typing import Protocol

from gamer_shop.application.dto import DiscrepancyDTO, NewDiscrepancyDTO


class DiscrepancyRepository(Protocol):
    """Журнал расхождений с поставщиком."""

    async def record(self, discrepancy: NewDiscrepancyDTO) -> bool:
        """Записать расхождение. False — такое уже записано."""
        ...

    async def resolve(self, discrepancy_id: int, resolution: str) -> None: ...

    async def open_ones(self, limit: int) -> list[DiscrepancyDTO]: ...

    async def all_for_request(self, request_id: str) -> list[DiscrepancyDTO]: ...

    async def counts_by_kind(self) -> dict[str, int]: ...
