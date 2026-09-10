"""Заглушка поставщика выдачи.

Отдельный процесс, а не мок в тестах: только так таймаут клиента настоящий
сетевой. В режиме зависания заглушка сначала выдаёт и запоминает код и лишь
потом перестаёт отвечать — клиент получает таймаут при уже выданном коде.

Этап 2 добавил недобросовестное поведение: заглушка умеет втихую отдать один
и тот же код дважды, подсунуть чужой код и ответить ошибкой, выдав код
на самом деле. Ответу такой заглушки доверять нельзя — и в этом весь смысл.

Он же добавил лимит. Считает его заглушка ведром токенов — так, как считают
настоящие поставщики: столько-то запросов в минуту плюс всплеск на полном
ведре. Всё сверх — 429, и факт превышения запоминается.

Отсюда проверка «лимит не превышается» становится наблюдаемой: магазин
держит собственное ведро с теми же числами и берёт из него токен перед
каждым запросом, поэтому его ведро всегда опустошается раньше. У честного
магазина over_limit остаётся нулём, сколько бы заказов ни пришло разом; у
магазина без учёта он немедленно вырастает.
"""

import asyncio
import json
import random
import time
from collections import deque
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
        # Ведро токенов и наблюдение за плотностью запросов.
        self._tokens = float(settings.rate_limit_burst)
        self._refilled_at = time.monotonic()
        self._hits: deque[float] = deque()
        self.over_limit = 0
        self.peak_per_minute = 0
        # Что заглушка считает выданным на самом деле — в том числе то,
        # о чём она соврала. По этому же журналу отвечает /issue/{request_id}.
        self.truth: dict[str, str] = {}

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
            self.truth[request_id] = code
            return code

    async def last_issued_code(self) -> str | None:
        """Последний выданный кому-либо код — для режима дубля."""
        async with self._lock:
            if not self.issued:
                return None
            return next(reversed(self.issued.values()))

    async def refill(self, codes: list[str]) -> int:
        async with self._lock:
            self._pool.extend(codes)
            return len(self._pool)

    def available(self) -> int:
        return len(self._pool)

    async def hit(self) -> bool:
        """Учесть запрос. False — лимит исчерпан, ответ будет 429.

        Ведро токенов, а не поминутные вёдра: на границе вёдер удвоенный
        поток проходит незамеченным, и лимит перестаёт что-либо значить.
        """
        limit = self._settings.rate_limit_per_minute
        async with self._lock:
            now = time.monotonic()
            while self._hits and now - self._hits[0] >= 60.0:
                self._hits.popleft()

            if limit:
                self._tokens = min(
                    float(self._settings.rate_limit_burst),
                    self._tokens + (now - self._refilled_at) * limit / 60.0,
                )
                self._refilled_at = now
                if self._tokens < 1.0:
                    self.over_limit += 1
                    return False
                self._tokens -= 1.0

            self._hits.append(now)
            self.peak_per_minute = max(self.peak_per_minute, len(self._hits))
            return True

    def reset_rate_counters(self) -> None:
        self._hits.clear()
        self._tokens = float(self._settings.rate_limit_burst)
        self._refilled_at = time.monotonic()
        self.over_limit = 0
        self.peak_per_minute = 0

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

    if not await state.hit():
        return Response(
            {'status': 'error', 'reason': 'rate_limited'},
            status_code=429,
            headers={'Retry-After': '1'},
        )

    if state.settings.latency:
        await asyncio.sleep(state.settings.latency)

    mode = state.roll()

    if mode == 'fail':
        return Response({'status': 'error', 'reason': 'internal_error'}, status_code=500)

    if mode == 'out_of_stock':
        return Response({'status': 'error', 'reason': 'out_of_stock'}, status_code=409)

    if mode == 'duplicate':
        # Втихую отдаём код, уже уехавший другому запросу. Свой код при этом
        # не тратится: снаружи ответ неотличим от честного.
        stolen = await state.last_issued_code()
        if stolen is not None:
            return Response(
                {'status': 'ok', 'request_id': request_id, 'code': stolen},
                status_code=200,
            )

    if mode == 'foreign_request_id':
        # Код настоящий, но подписан чужим request_id: ответ не про
        # тот запрос, который задавали.
        code = await state.take_code(request_id)
        return Response(
            {'status': 'ok', 'request_id': f'{request_id}-ГХ', 'code': code},
            status_code=200,
        )

    code = await state.take_code(request_id)
    if code is None:
        return Response({'status': 'error', 'reason': 'out_of_stock'}, status_code=409)

    if mode == 'lie_error':
        # Код выдан и записан, а наружу уходит ошибка. Повтор с тем же
        # request_id обязан вернуть его же — иначе выдач станет две.
        return Response({'status': 'error', 'reason': 'internal_error'}, status_code=500)

    if mode in ('timeout', 'dark'):
        # Код уже выдан и запомнен, а ответ не дойдёт.
        await asyncio.sleep(state.settings.hang_seconds)

    return Response({'status': 'ok', 'request_id': request_id, 'code': code}, status_code=200)


@get('/issue/{request_id:str}')
async def issue_status(request_id: str) -> Response:
    """Что поставщик выдал по этому запросу на самом деле.

    Единственный способ узнать правду, когда ответу на /issue доверять
    нельзя. Отвечает по журналу, а не по последнему ответу.
    """
    if not await state.hit():
        return Response(
            {'status': 'error', 'reason': 'rate_limited'},
            status_code=429,
            headers={'Retry-After': '1'},
        )

    if state.forced_mode == 'dark':
        # Поставщик молчит совсем: правды не узнать, и это единственный
        # случай, когда неопределённость сохраняется надолго.
        await asyncio.sleep(state.settings.hang_seconds)

    code = state.truth.get(request_id)
    if code is None:
        return Response(
            {'status': 'not_found', 'request_id': request_id}, status_code=404
        )
    return Response(
        {'status': 'ok', 'request_id': request_id, 'code': code}, status_code=200
    )


@post('/_control', status_code=200)
async def control(data: dict) -> dict:
    """mode: ok | fail | timeout | dark | out_of_stock | duplicate |
    foreign_request_id | lie_error | random.

    Здесь же задаётся лимит (rate_limit_per_minute, rate_limit_burst) и
    сбрасываются его счётчики (reset_rate_counters).
    """
    mode = data.get('mode', 'random')
    state.forced_mode = None if mode == 'random' else mode
    if 'hang_seconds' in data:
        state.settings.hang_seconds = float(data['hang_seconds'])
    if 'rate_limit_per_minute' in data:
        state.settings.rate_limit_per_minute = int(data['rate_limit_per_minute'])
    if 'rate_limit_burst' in data:
        state.settings.rate_limit_burst = int(data['rate_limit_burst'])
    if data.get('reset_rate_counters'):
        state.reset_rate_counters()
    return {
        'status': 'ok',
        'mode': mode,
        'hang_seconds': state.settings.hang_seconds,
        'rate_limit_per_minute': state.settings.rate_limit_per_minute,
        'rate_limit_burst': state.settings.rate_limit_burst,
    }


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
        # Правда о выданном: включает то, о чём заглушка соврала.
        'truth': state.truth,
        'truth_count': len(state.truth),
        'forced_mode': state.forced_mode,
        'rate_limit_per_minute': state.settings.rate_limit_per_minute,
        'rate_limit_burst': state.settings.rate_limit_burst,
        # Ноль — лимит соблюдался. Любое другое число означает, что магазин
        # обогнал поставщика.
        'over_limit': state.over_limit,
        'peak_per_minute': state.peak_per_minute,
    }


@get('/health')
async def health() -> dict:
    return {'status': 'ok', 'supplier': state.settings.name}


def create_app() -> Litestar:
    global state
    state = SupplierState(StubSettings())
    return Litestar(
        route_handlers=[issue, issue_status, control, refill, stats, health]
    )


app = create_app()
