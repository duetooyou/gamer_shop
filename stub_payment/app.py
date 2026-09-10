"""Заглушка платёжной системы: возвраты.

Отдельный процесс, как и заглушка поставщика: только так таймаут клиента
настоящий сетевой. В режиме зависания возврат сначала выполняется и
запоминается и лишь потом перестаёт отвечать — клиент получает таймаут при
уже ушедших деньгах.
"""

import asyncio
import random

from litestar import Litestar, get, post
from litestar.response import Response

from stub_payment.config import StubPaymentSettings


class PaymentState:
    def __init__(self, settings: StubPaymentSettings) -> None:
        self._settings = settings
        self._rnd = random.Random(settings.seed)
        self._lock = asyncio.Lock()
        # refund_id -> сумма: повтор возвращает тот же ответ, а не второй возврат.
        self.refunds: dict[str, dict] = {}
        self.forced_mode: str | None = None

    @property
    def settings(self) -> StubPaymentSettings:
        return self._settings

    async def register(self, refund_id: str, payload: dict) -> tuple[dict, bool]:
        """Возврат и признак того, что он уже был."""
        async with self._lock:
            if refund_id in self.refunds:
                return self.refunds[refund_id], True
            self.refunds[refund_id] = payload
            return payload, False

    def roll(self) -> str:
        if self.forced_mode is not None:
            return self.forced_mode
        r = self._rnd.random()
        if r < self._settings.timeout_rate:
            return 'timeout'
        if r < self._settings.timeout_rate + self._settings.fail_rate:
            return 'fail'
        return 'ok'


state: PaymentState


@post('/refund', status_code=200)
async def refund(data: dict) -> Response:
    refund_id = data.get('refund_id')
    order_id = data.get('order_id')
    amount = data.get('amount')
    if not refund_id or not order_id or amount is None:
        return Response({'status': 'error', 'reason': 'bad_request'}, status_code=400)

    if state.settings.latency:
        await asyncio.sleep(state.settings.latency)

    mode = state.roll()
    if mode == 'fail':
        return Response({'status': 'error', 'reason': 'internal_error'}, status_code=500)

    record, existed = await state.register(
        refund_id,
        {
            'refund_id': refund_id,
            'order_id': order_id,
            'amount': amount,
            'currency': data.get('currency', 'RUB'),
            'reason': data.get('reason'),
        },
    )

    if mode == 'timeout':
        # Деньги уже ушли и записаны, а ответ не дойдёт.
        await asyncio.sleep(state.settings.hang_seconds)

    return Response(
        {'status': 'ok', 'duplicate': existed, **record}, status_code=200
    )


@post('/_control', status_code=200)
async def control(data: dict) -> dict:
    """mode: ok | fail | timeout | random."""
    mode = data.get('mode', 'random')
    state.forced_mode = None if mode == 'random' else mode
    if 'hang_seconds' in data:
        state.settings.hang_seconds = float(data['hang_seconds'])
    return {'status': 'ok', 'mode': mode, 'hang_seconds': state.settings.hang_seconds}


@get('/_stats')
async def stats() -> dict:
    """Счётчики для тестов: сумма возвратов и их число."""
    return {
        'refund_count': len(state.refunds),
        'refunded_amount': sum(r['amount'] for r in state.refunds.values()),
        'refunds': state.refunds,
        'forced_mode': state.forced_mode,
    }


@get('/health')
async def health() -> dict:
    return {'status': 'ok', 'service': 'payments'}


def create_app() -> Litestar:
    global state
    state = PaymentState(StubPaymentSettings())
    return Litestar(route_handlers=[refund, control, stats, health])


app = create_app()
