from .base import ResponseBase


class WebhookAckOut(ResponseBase):
    """Всегда 200. Поле outcome — для логов и тестов, платёжке оно не важно."""

    status: str = 'accepted'
    outcome: str
    order_id: str
