"""Прогресс разбора всплеска: сколько в очереди и сколько уже выдано."""

from gamer_shop.application.dto import ProgressDTO
from gamer_shop.application.repositories import ProgressRepository


class ProgressInteractor:
    def __init__(self, progress: ProgressRepository) -> None:
        self._progress = progress

    async def execute(self) -> ProgressDTO:
        return await self._progress.snapshot()
