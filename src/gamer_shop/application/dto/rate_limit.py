from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateLimitDecisionDTO:
    """Исход запроса разрешения на одно обращение к поставщику."""

    granted: bool
    # Через сколько секунд появится следующий токен. Осмысленно при отказе:
    # именно на этот срок команда уходит обратно в очередь.
    retry_after_seconds: float
    tokens_left: float


@dataclass(frozen=True, slots=True)
class RateLimitStateDTO:
    """Состояние ведра — часть отчёта о прогрессе."""

    supplier: str
    capacity: int
    # Наш темп: объявленный лимит поставщика минус запас.
    pace_per_minute: int
    available: float
    granted: int
    throttled: int
