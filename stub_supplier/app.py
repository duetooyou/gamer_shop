"""Заглушка поставщика выдачи.

Отдельный процесс, а не мок в тестах: только так таймаут клиента настоящий
сетевой. В режиме зависания заглушка сначала выдаёт и запоминает код и лишь
потом перестаёт отвечать — клиент получает таймаут при уже выданном коде.
"""

import asyncio
import json
import random
from pathlib import Path

from litestar import Litestar, get, post
from litestar.response import Response

from stub_supplier.config import StubSettings


class SupplierState:
    def __init__(self, settings: StubSettings) -> None:
        self._settings = settings
        self._rnd = random.Random(settings.seed)
        self._lock = asyncio.Lock()

        raw = json.loads(Path(settings.keys_file).read_text(encoding='utf-8'))
        pool = raw['keys'][settings.keys_offset :: settings.keys_stride]
        self._pool: list[str] = list(pool)

        # request_id -> код: повтор возвращает тот же код, а не новый.
        self.issued: dict[str, str] = {}
        self.forced_mode: str | None = None

    @property
    def settings(self) -> StubSettings:
        return self._settings

    async def take_code(self, request_id: str) -> str | None:
        """Код под request_id, None если пул исчерпан.

        Под замком: два параллельных запроса с одним request_id получат
        один код, а не два.
        """
        async with self._lock:
            if request_id in self.issued:
                return self.issued[request_id]
            if not self._pool:
                return None
            code = self._pool.pop(0)
            self.issued[request_id] = code
            return code

    async def refill(self, codes: list[str]) -> int:
        async with self._lock:
            self._pool.extend(codes)
            return len(self._pool)

    def available(self) -> int:
        return len(self._pool)

    def roll(self) -> str:
    
        if self.forced_mode is not None:
            return self.forced_mode
        r = self._rnd.random()
        if r < self._settings.timeout_rate:
            return 'timeout'
        if r < self._settings.timeout_rate + self._settings.fail_rate:
            return 'fail'
        return 'ok'


state: SupplierState


@post('/issue', status_code=200)
async def issue(data: dict) -> Response:
    request_id = data.get('request_id')
    sku = data.get('sku')
    order_id = data.get('order_id')
    if not request_id or not sku or not order_id:
        return Response({'status': 'error', 'reason': 'bad_request'}, status_code=400)

    if state.settings.latency:
        await asyncio.sleep(state.settings.latency)

    mode = state.roll()

    if mode == 'fail':
        return Response({'status': 'error', 'reason': 'internal_error'}, status_code=500)

    if mode == 'out_of_stock':
        return Response({'status': 'error', 'reason': 'out_of_stock'}, status_code=409)

    code = await state.take_code(request_id)
    if code is None:
        return Response({'status': 'error', 'reason': 'out_of_stock'}, status_code=409)

    if mode == 'timeout':
        # Код уже выдан и запомнен, а ответ не дойдёт.
        await asyncio.sleep(state.settings.hang_seconds)

    return Response({'status': 'ok', 'request_id': request_id, 'code': code}, status_code=200)


@post('/_control', status_code=200)
async def control(data: dict) -> dict:
    """mode: ok | fail | timeout | out_of_stock | random."""
    mode = data.get('mode', 'random')
    state.forced_mode = None if mode == 'random' else mode
    if 'hang_seconds' in data:
        state.settings.hang_seconds = float(data['hang_seconds'])
    return {'status': 'ok', 'mode': mode, 'hang_seconds': state.settings.hang_seconds}


@post('/_refill', status_code=200)
async def refill(data: dict) -> dict:
    """Пополнение пула."""
    codes = data.get('codes')
    if not codes:
        count = int(data.get('count', 10))
        prefix = state.settings.name.upper()
        start = len(state.issued) + state.available()
        codes = [f'{prefix}RFL-{start + i:04d}-NEW' for i in range(count)]
    total = await state.refill(list(codes))
    return {'status': 'ok', 'added': len(codes), 'available': total}


@get('/_stats')
async def stats() -> dict:
    """Счётчики для тестов."""
    return {
        'supplier': state.settings.name,
        'available': state.available(),
        'issued_count': len(state.issued),
        'distinct_codes': len(set(state.issued.values())),
        'issued': state.issued,
        'forced_mode': state.forced_mode,
    }


@get('/health')
async def health() -> dict:
    return {'status': 'ok', 'supplier': state.settings.name}


def create_app() -> Litestar:
    global state
    state = SupplierState(StubSettings())
    return Litestar(route_handlers=[issue, control, refill, stats, health])


app = create_app()
