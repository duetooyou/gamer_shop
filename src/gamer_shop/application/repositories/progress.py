from typing import Protocol

from gamer_shop.application.dto import ProgressDTO


class ProgressRepository(Protocol):
    """Прогресс разбора всплеска.

    Считается по состоянию — очереди, позициям, ведру токенов, — а не по
    счётчикам в памяти: перезапуск процесса не должен обнулять картину.
    """

    async def snapshot(self) -> ProgressDTO: ...
