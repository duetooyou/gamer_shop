from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from gamer_shop.application.enums import PaymentStatus


@dataclass(frozen=True, slots=True)
class PaymentWebhookDTO:
    """Полезная нагрузка вебхука оплаты."""

    event_id: str
    order_id: str
    status: PaymentStatus
    amount: int
    currency: str
    created_at: datetime | None
    raw: dict[str, Any]


class WebhookOutcome(StrEnum):
    """Наружу всегда 200, различие — для логов и тестов."""

    APPLIED = 'applied'
    DUPLICATE = 'duplicate'
    ALREADY_FINAL = 'already_final'
    ORDER_NOT_FOUND = 'order_not_found'  # вебхук раньше заказа
    AMOUNT_MISMATCH = 'amount_mismatch'


@dataclass(frozen=True, slots=True)
class WebhookResultDTO:
    outcome: WebhookOutcome
    order_id: str
    # В очередь ставим только после коммита транзакции вебхука.
    schedule_delivery: bool = False
