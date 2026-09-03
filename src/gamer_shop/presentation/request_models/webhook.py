from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PaymentWebhookIn(BaseModel):
    """Вебхук оплаты. Подпись не проверяем — по условию задания."""

    event_id: str = Field(
        ..., min_length=1, max_length=128,
        description='Уникален для события; повтор приходит с тем же значением',
    )
    order_id: str = Field(..., min_length=1, max_length=32)
    status: Literal['paid', 'failed']
    amount: int = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3)
    created_at: datetime | None = None
