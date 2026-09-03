from dishka import FromDishka
from dishka.integrations.litestar import inject
from litestar import Controller, post
from litestar.status_codes import HTTP_200_OK

from gamer_shop.application.dto import PaymentWebhookDTO
from gamer_shop.application.enums import PaymentStatus
from gamer_shop.application.interactors import HandlePaymentWebhookInteractor
from gamer_shop.application.interfaces import DeliveryScheduler
from gamer_shop.presentation.request_models import PaymentWebhookIn
from gamer_shop.presentation.response_models import WebhookAckOut


class PaymentWebhookController(Controller):
    tags = ['Вебхуки']

    @post(
        '/webhook/payment',
        status_code=HTTP_200_OK,
        summary='Вебхук платёжной системы',
        description=(
            'Идемпотентен по event_id, выдерживает параллельные и перепутанные '
            'по порядку доставки. Всегда отвечает 200: 5xx заставил бы платёжную '
            'систему повторять доставку.'
        ),
    )
    @inject
    async def payment(
        self,
        data: PaymentWebhookIn,
        interactor: FromDishka[HandlePaymentWebhookInteractor],
        scheduler: FromDishka[DeliveryScheduler],
    ) -> WebhookAckOut:
        result = await interactor.execute(
            PaymentWebhookDTO(
                event_id=data.event_id,
                order_id=data.order_id,
                status=PaymentStatus(data.status),
                amount=data.amount,
                currency=data.currency,
                created_at=data.created_at,
                raw=data.model_dump(mode='json'),
            )
        )
        # Только после коммита: иначе воркер прочитает заказ в старом состоянии.
        if result.schedule_delivery:
            await scheduler.schedule(result.order_id)

        return WebhookAckOut(outcome=result.outcome.value, order_id=result.order_id)
