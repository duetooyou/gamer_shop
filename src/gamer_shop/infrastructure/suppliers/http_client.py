"""HTTP-клиент к поставщикам.

Единственное место, где сетевой сбой и недобросовестный ответ превращаются
в исход:

* OK       — ответ 200, подписанный тем же request_id, с кодом. Верим.
* REFUSED  — запрос заведомо не дошёл (соединение не установилось). Кода нет,
             фолбэк разрешён.
* UNKNOWN  — всё остальное. Ответа нет либо ответу нельзя верить: код мог
             быть выдан, и фолбэк запрещён до запроса состояния.

Отличие от первого этапа в том, что 5xx больше не считается отказом. Раньше
завершённый ответ означал, что поставщик отработал запрос и кода не выдал;
теперь он может выдать код и ответить ошибкой, так что верить нечему.
"""

import httpx

from gamer_shop.application.dto import SupplierOutcome, SupplierOutcomeKind
from gamer_shop.application.enums import SupplierName
from gamer_shop.application.interfaces import DeliveryLogger
from gamer_shop.infrastructure.config import SupplierSettings

# Ответ пришёл не про наш запрос: подпись не совпала.
FOREIGN_RESPONSE = 'foreign_response'

# Поставщик отказался даже смотреть: лимит исчерпан.
RATE_LIMITED = 'rate_limited'


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
        self, supplier: SupplierName, request_id: str, sku: str, order_item_id: str
    ) -> SupplierOutcome:
        log = self._logger.bind(
            order_item_id=order_item_id,
            sku=sku,
            supplier=supplier.value,
            request_id=request_id,
        )
        url = f'{self._urls[supplier]}/issue'
        payload = {'request_id': request_id, 'sku': sku, 'order_id': order_item_id}

        def result(kind, *, code=None, reason=None, answered=False) -> SupplierOutcome:
            log.info(
                'supplier_attempt',
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
                attempts=1,
                answered=answered,
            )

        try:
            response = await self._client.post(
                url, json=payload, timeout=self._settings.request_timeout
            )
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            # Соединение не установлено — запрос не дошёл, кода нет.
            return result(
                SupplierOutcomeKind.REFUSED, reason=f'connect_error: {type(exc).__name__}'
            )
        except httpx.HTTPError as exc:
            # Запрос ушёл, ответа нет: код мог быть выдан.
            return result(SupplierOutcomeKind.UNKNOWN, reason=f'timeout: {type(exc).__name__}')

        if response.status_code == 429:
            # Отказ по лимиту — единственный завершённый ответ, которому можно
            # верить без запроса состояния: поставщик до выдачи не дошёл.
            # В норме сюда не попадаем — ведро токенов не пускает; если
            # попали, значит наш учёт разошёлся с чужим, и это видно в логе.
            log.warning('supplier_rate_limited', supplier=supplier.value)
            return result(SupplierOutcomeKind.REFUSED, reason=RATE_LIMITED, answered=True)

        body = self._body(response)

        if response.status_code == 200 and body.get('status') == 'ok' and body.get('code'):
            if body.get('request_id') != request_id:
                # Ответ не про наш запрос: код настоящий, но чей — неизвестно.
                # Брать его нельзя, а считать отказом нечестно.
                return result(
                    SupplierOutcomeKind.UNKNOWN, reason=FOREIGN_RESPONSE, answered=True
                )
            return result(SupplierOutcomeKind.OK, code=str(body['code']), answered=True)

        # Отказ по существу тоже не доказательство: поставщик мог выдать код
        # и ответить ошибкой. Разбирается запросом состояния.
        return result(
            SupplierOutcomeKind.UNKNOWN,
            reason=str(body.get('reason') or f'http_{response.status_code}'),
            answered=True,
        )

    async def probe(self, supplier: SupplierName, request_id: str) -> SupplierOutcome:
        log = self._logger.bind(supplier=supplier.value, request_id=request_id)
        url = f'{self._urls[supplier]}/issue/{request_id}'

        def result(kind, *, code=None, reason=None) -> SupplierOutcome:
            log.info('supplier_probe', outcome=kind.value, reason=reason,
                     has_code=code is not None)
            return SupplierOutcome(
                kind=kind,
                request_id=request_id,
                supplier=supplier,
                code=code,
                reason=reason,
            )

        try:
            response = await self._client.get(
                url, timeout=self._settings.request_timeout
            )
        except httpx.HTTPError as exc:
            # Правды не узнали — состояние остаётся неопределённым.
            return result(SupplierOutcomeKind.UNKNOWN, reason=f'probe_failed: {type(exc).__name__}')

        if response.status_code == 404:
            # Поставщик не знает такого запроса: кода нет, фолбэк разрешён.
            return result(SupplierOutcomeKind.REFUSED, reason='not_issued')

        if response.status_code == 429:
            # Правды не узнали: отказ по лимиту ничего не говорит о коде.
            return result(SupplierOutcomeKind.UNKNOWN, reason=RATE_LIMITED)

        body = self._body(response)
        if response.status_code == 200 and body.get('code'):
            if body.get('request_id') != request_id:
                return result(SupplierOutcomeKind.UNKNOWN, reason=FOREIGN_RESPONSE)
            return result(SupplierOutcomeKind.OK, code=str(body['code']))

        return result(
            SupplierOutcomeKind.UNKNOWN,
            reason=str(body.get('reason') or f'http_{response.status_code}'),
        )

    @staticmethod
    def _body(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}
