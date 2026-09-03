from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Время как зависимость — тестам его нужно сдвигать."""

    def now(self) -> datetime: ...
