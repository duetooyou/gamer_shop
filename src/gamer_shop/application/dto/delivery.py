from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from gamer_shop.application.enums import SupplierName, SupplierRequestState


class SupplierOutcomeKind(StrEnum):
    """Три состояния, а не «успех/ошибка»: различие REFUSED и UNKNOWN
    определяет, можно ли уходить на резервного поставщика."""

    OK = 'ok'
    # Код точно не выдан — фолбэк разрешён.
    REFUSED = 'refused'
    # Ответа нет, код мог быть выдан — фолбэк запрещён.
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class SupplierOutcome:
    kind: SupplierOutcomeKind
    request_id: str
    supplier: SupplierName
    code: str | None = None
    reason: str | None = None
    attempts: int = 0
    # Ответ дошёл целиком. Разница существенная: молчание — это обрыв связи,
    # а завершённый ответ с ошибкой при выданном коде — уже недобросовестность,
    # и в журнал расхождений попадает только вторая.
    answered: bool = False


@dataclass(frozen=True, slots=True)
class SupplierRequestSnapshot:
    """Что известно об обращении до нового вызова.

    Если состояние уже ok, код получен ранее — второй раз не идём вовсе.
    """

    state: SupplierRequestState
    request_id: str
    supplier: SupplierName
    code: str | None = None
    reason: str | None = None
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class DeliveryDTO:
    order_item_id: str
    code: str
    supplier: str
    request_id: str
    delivered_at: datetime


class DeliveryResultKind(StrEnum):
    DELIVERED = 'delivered'
    ALREADY_DELIVERED = 'already_delivered'
    OUT_OF_STOCK = 'out_of_stock'
    DELIVERY_FAILED = 'delivery_failed'
    # Поставщик не ответил, судьба кода неизвестна — дожмёт фоновая задача.
    PENDING_UNKNOWN = 'pending_unknown'
    # Позиция занята другим воркером либо не в статусе, из которого выдают.
    SKIPPED = 'skipped'


@dataclass(frozen=True, slots=True)
class DeliveryResultDTO:
    kind: DeliveryResultKind
    order_item_id: str
    code: str | None = None
    supplier: str | None = None
    reason: str | None = None
