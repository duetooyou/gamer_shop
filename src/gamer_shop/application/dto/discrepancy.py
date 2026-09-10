from dataclasses import dataclass
from datetime import datetime

from gamer_shop.application.enums import DiscrepancyKind, SupplierName


@dataclass(frozen=True, slots=True)
class NewDiscrepancyDTO:
    kind: DiscrepancyKind
    supplier: SupplierName
    request_id: str
    order_item_id: str | None = None
    code: str | None = None
    detail: str | None = None
    # Разбор чаще всего происходит там же, где обнаружение: расхождение и
    # ответ на него — один и тот же шаг.
    resolution: str | None = None


@dataclass(frozen=True, slots=True)
class DiscrepancyDTO:
    id: int
    kind: str
    supplier: str
    request_id: str
    order_item_id: str | None
    code: str | None
    detail: str | None
    state: str
    resolution: str | None
    created_at: datetime
    resolved_at: datetime | None
