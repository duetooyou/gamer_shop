"""HTTP-клиент к поставщикам.

Единственное место, где сетевой сбой превращается в исход:
OK / REFUSED (кода точно нет, фолбэк можно) / UNKNOWN (ответа нет, фолбэк нельзя).
Разница между 5xx и таймаутом чтения в том, что 5xx — завершённый ответ,
а таймаут — незавершённый запрос.
"""

import asyncio

import httpx

from gamer_shop.application.dto import SupplierOutcome, SupplierOutcomeKind
from gamer_shop.application.enums import SupplierName
from gamer_shop.application.interfaces import DeliveryLogger
from gamer_shop.application.policies import backoff_delay
from gamer_shop.infrastructure.config import SupplierSettings

_RETRIABLE = (SupplierOutcomeKind.UNKNOWN,)


class SupplierHttpClient:
    def __init__(
        self,
        settings: SupplierSettings,
        client: httpx.AsyncClient,
        logger: DeliveryLogger,
    ) -> None:
        self._settings = settings
        self._client = client
        self._logger = logger
        self._urls = {
            SupplierName.A: settings.primary_url.rstrip('/'),
            SupplierName.B: settings.fallback_url.rstrip('/'),
        }

    async def issue(
        self, supplier: SupplierName, request_id: str, sku: str, order_id: str
    ) -> SupplierOutcome:
        """Запросить код. Повторы идут тем же request_id: по контракту
        поставщик обязан вернуть на повтор тот же код, так что повтор
        работает как запрос состояния.
        """
        log = self._logger.bind(
            order_id=order_id, sku=sku, supplier=supplier.value, request_id=request_id
        )
        outcome = SupplierOutcome(
            kind=SupplierOutcomeKind.UNKNOWN, request_id=request_id, supplier=supplier
        )

        for attempt in range(1, self._settings.max_attempts + 1):
            if attempt > 1:
                delay = backoff_delay(
                    attempt - 1,
                    self._settings.backoff_base,
                    self._settings.backoff_max,
                    self._settings.backoff_jitter,
                )
                log.info('supplier_retry', attempt=attempt, delay=round(delay, 3))
                await asyncio.sleep(delay)

            outcome = await self._attempt(supplier, request_id, sku, order_id, attempt, log)
            if outcome.kind not in _RETRIABLE:
                break

        return SupplierOutcome(
            kind=outcome.kind,
            request_id=request_id,
            supplier=supplier,
            code=outcome.code,
            reason=outcome.reason,
            attempts=outcome.attempts,
        )

    async def _attempt(
        self,
        supplier: SupplierName,
        request_id: str,
        sku: str,
        order_id: str,
        attempt: int,
        log,
    ) -> SupplierOutcome:
        url = f'{self._urls[supplier]}/issue'
        payload = {'request_id': request_id, 'sku': sku, 'order_id': order_id}

        def result(kind: SupplierOutcomeKind, *, code=None, reason=None) -> SupplierOutcome:
            log.info(
                'supplier_attempt',
                attempt=attempt,
                outcome=kind.value,
                reason=reason,
                has_code=code is not None,
            )
            return SupplierOutcome(
                kind=kind,
                request_id=request_id,
                supplier=supplier,
                code=code,
                reason=reason,
                attempts=attempt,
            )

        try:
            response = await self._client.post(
                url, json=payload, timeout=self._settings.request_timeout
            )
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            # Соединение не установлено — запрос не дошёл, кода нет.
            return result(SupplierOutcomeKind.REFUSED, reason=f'connect_error: {type(exc).__name__}')
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            # Запрос ушёл, ответа нет: код мог быть выдан.
            return result(SupplierOutcomeKind.UNKNOWN, reason=f'timeout: {type(exc).__name__}')
        except httpx.HTTPError as exc:
            return result(SupplierOutcomeKind.UNKNOWN, reason=f'http_error: {type(exc).__name__}')

        if response.status_code >= 500:
            # Ответ завершён: поставщик отработал запрос и отказал.
            return result(SupplierOutcomeKind.REFUSED, reason=f'http_{response.status_code}')

        try:
            body = response.json()
        except ValueError:
            return result(SupplierOutcomeKind.REFUSED, reason='invalid_json')

        if response.status_code == 200 and body.get('status') == 'ok' and body.get('code'):
            return result(SupplierOutcomeKind.OK, code=str(body['code']))

        return result(
            SupplierOutcomeKind.REFUSED,
            reason=str(body.get('reason') or f'http_{response.status_code}'),
        )
