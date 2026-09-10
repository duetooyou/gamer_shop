"""HTTP-клиент к платёжной системе.

Различие исходов то же, что и у поставщика: REFUSED — деньги точно не ушли,
повтор безопасен; UNKNOWN — ответа нет, деньги могли уйти, и повторять можно
только тем же refund_id.
"""

import httpx

from gamer_shop.application.interfaces import (
    PaymentsLogger,
    RefundOutcome,
    RefundOutcomeKind,
)
from gamer_shop.infrastructure.config import PaymentSettings


class PaymentHttpGateway:
    def __init__(
        self,
        settings: PaymentSettings,
        client: httpx.AsyncClient,
        logger: PaymentsLogger,
    ) -> None:
        self._settings = settings
        self._client = client
        self._logger = logger

    async def refund(
        self,
        refund_id: str,
        order_id: str,
        amount: int,
        currency: str,
        reason: str,
    ) -> RefundOutcome:
        log = self._logger.bind(refund_id=refund_id, order_id=order_id, amount=amount)
        url = f'{self._settings.url.rstrip("/")}/refund'
        payload = {
            'refund_id': refund_id,
            'order_id': order_id,
            'amount': amount,
            'currency': currency,
            'reason': reason,
        }

        try:
            response = await self._client.post(
                url, json=payload, timeout=self._settings.request_timeout
            )
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            # Соединение не установлено — запрос не дошёл, деньги на месте.
            log.warning('refund_connect_error', error=type(exc).__name__)
            return RefundOutcome(
                RefundOutcomeKind.REFUSED, refund_id, f'connect_error: {type(exc).__name__}'
            )
        except httpx.HTTPError as exc:
            # Запрос ушёл, ответа нет: деньги могли уйти.
            log.warning('refund_unknown', error=type(exc).__name__)
            return RefundOutcome(
                RefundOutcomeKind.UNKNOWN, refund_id, f'timeout: {type(exc).__name__}'
            )

        if response.status_code >= 500:
            # Ответ завершён: платёжка отработала запрос и отказала.
            return RefundOutcome(
                RefundOutcomeKind.REFUSED, refund_id, f'http_{response.status_code}'
            )

        try:
            body = response.json()
        except ValueError:
            return RefundOutcome(RefundOutcomeKind.REFUSED, refund_id, 'invalid_json')

        if response.status_code == 200 and body.get('status') == 'ok':
            log.info('refund_accepted', duplicate=bool(body.get('duplicate')))
            return RefundOutcome(RefundOutcomeKind.OK, refund_id)

        return RefundOutcome(
            RefundOutcomeKind.REFUSED,
            refund_id,
            str(body.get('reason') or f'http_{response.status_code}'),
        )
