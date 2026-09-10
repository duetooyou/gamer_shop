from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gamer_shop.application.enums import (
    DEFAULT_PARTITION,
    PRIORITY_NORMAL,
    OutboxCommandKind,
)


@dataclass(frozen=True, slots=True)
class NewCommandDTO:
    """Команда на постановку. dedup_key отвечает за то, что повтор шага
    не создаст вторую незавершённую команду на тот же предмет."""

    kind: OutboxCommandKind
    dedup_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    partition_key: str = DEFAULT_PARTITION
    priority: int = PRIORITY_NORMAL
    delay_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class OutboxCommandDTO:
    """Команда, взятая в работу.

    Вид — строка, а не перечисление: в таблице может лежать команда,
    записанная другой версией кода, и разбор пачки не должен на ней падать.
    Незнакомый вид пройдёт обычным путём отказа и попадёт в сверку.
    """

    id: int
    kind: str
    dedup_key: str
    payload: dict[str, Any]
    partition_key: str
    priority: int
    attempts: int


@dataclass(frozen=True, slots=True)
class OutboxStatsDTO:
    """Глубина очереди — по ней виден прогресс."""

    partition_key: str
    kind: str
    state: str
    count: int
    oldest_available_at: datetime | None


@dataclass(frozen=True, slots=True)
class RelayPassDTO:
    """Итог одного прохода релея.

    Взятых команд мало не потому, что работа кончилась: очередь могла
    упереться в лимит поставщика. Различать эти два случая обязан тот, кто
    решает, звать релей снова или подождать планового тика.
    """

    taken: int
    # Партиции, у которых работа есть, а разрешений нет.
    throttled: tuple[str, ...] = ()
    retry_after_seconds: float = 0.0

    @property
    def more(self) -> bool:
        return bool(self.throttled)
