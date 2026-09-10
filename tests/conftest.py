"""Фикстуры тестов.

Настоящий PostgreSQL и настоящие HTTP-заглушки в отдельных процессах: гонки
и таймауты на моках непроверяемы. Между тестами TRUNCATE, а не откат — иначе
параллельные запросы не увидят друг друга. Последовательность order_id при
этом не сбрасывается: заглушка помнит коды по request_id, выведенному из него.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent

# Отдельная база: прогон не должен затирать данные разработки.
os.environ['POSTGRES_DATABASE'] = os.environ.get('TEST_POSTGRES_DATABASE', 'gamer_shop_test')
os.environ.setdefault('APP_LOG_LEVEL', 'WARNING')
os.environ.setdefault('APP_LOG_JSON', 'false')

from dishka import Provider, Scope, make_async_container, provide  # noqa: E402
from sqlalchemy import text  # noqa: E402

from gamer_shop.application.interactors import OutboxRelayInteractor  # noqa: E402
from gamer_shop.application.interfaces import OutboxNotifier  # noqa: E402
from gamer_shop.infrastructure.config import Config  # noqa: E402
from gamer_shop.infrastructure.database import new_session_maker  # noqa: E402
from gamer_shop.infrastructure.tasks import InlineOutboxNotifier  # noqa: E402
from gamer_shop.ioc import setup_providers  # noqa: E402

TABLES = (
    'outbox',
    'order_events',
    'supplier_rate_limits',
    'supplier_discrepancies',
    'ledger_entries',
    'deliveries',
    'supplier_requests',
    'payment_events',
    'order_items',
    'orders',
    'product_stock',
    'products',
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class InlineNotifierProvider(Provider):
    """Релей крутится прямо в обработчике: сквозной путь без воркера."""

    @provide(scope=Scope.REQUEST)
    def notifier(self, relay: OutboxRelayInteractor) -> OutboxNotifier:
        return InlineOutboxNotifier(relay)


class DeadOutboxNotifier:
    """Побудка не доходит: воркер лёг, Redis недоступен, процесс умер сразу
    после коммита. Всё, что остаётся, — запись в таблице."""

    async def notify(self) -> None:
        return None


class DeadNotifierProvider(Provider):
    @provide(scope=Scope.REQUEST)
    def notifier(self) -> OutboxNotifier:
        return DeadOutboxNotifier()


class StubSupplier:


    def __init__(self, name: str, offset: int, port: int) -> None:
        self.name = name
        self.port = port
        self.url = f'http://127.0.0.1:{port}'
        env = {
            **os.environ,
            'STUB_NAME': name,
            'STUB_KEYS_FILE': str(ROOT / 'data' / 'keys.json'),
            'STUB_KEYS_OFFSET': str(offset),
            'STUB_KEYS_STRIDE': '2',
            'STUB_FAIL_RATE': '0.0',
            'STUB_TIMEOUT_RATE': '0.0',
            'STUB_HANG_SECONDS': '10',
        }
        self._proc = subprocess.Popen(
            [
                sys.executable, '-m', 'granian', '--interface', 'asgi',
                'stub_supplier.app:app', '--host', '127.0.0.1', '--port', str(port),
            ],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def wait_ready(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if httpx.get(f'{self.url}/health', timeout=0.5).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.15)
        raise RuntimeError(f'Заглушка {self.name} не поднялась на порту {self.port}')

    def set_mode(self, mode: str, hang_seconds: float | None = None) -> None:
        payload: dict = {'mode': mode}
        if hang_seconds is not None:
            payload['hang_seconds'] = hang_seconds
        httpx.post(f'{self.url}/_control', json=payload, timeout=5.0).raise_for_status()

    def set_rate_limit(self, per_minute: int, burst: int) -> None:
        httpx.post(
            f'{self.url}/_control',
            json={
                'mode': self.stats()['forced_mode'] or 'random',
                'rate_limit_per_minute': per_minute,
                'rate_limit_burst': burst,
                'reset_rate_counters': True,
            },
            timeout=5.0,
        ).raise_for_status()

    def stats(self) -> dict:
        return httpx.get(f'{self.url}/_stats', timeout=5.0).json()

    def issued_count(self) -> int:
        return self.stats()['issued_count']

    def available(self) -> int:
        return self.stats()['available']

    def refill(self, count: int = 10) -> None:
        httpx.post(f'{self.url}/_refill', json={'count': count}, timeout=5.0).raise_for_status()

    def stop(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


@pytest.fixture(scope='session', autouse=True)
def prepare_database() -> None:
    """Создать базу и накатить миграции один раз на прогон."""
    import asyncio

    import asyncpg

    cfg = Config().postgres

    async def ensure() -> None:
        conn = await asyncpg.connect(
            host=cfg.host, port=cfg.port, user=cfg.user,
            password=cfg.password, database='postgres',
        )
        exists = await conn.fetchval(
            'SELECT 1 FROM pg_database WHERE datname = $1', cfg.database
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{cfg.database}"')
        await conn.close()

    asyncio.run(ensure())
    subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
        cwd=ROOT, check=True, capture_output=True,
    )


class StubPayment:
    """Заглушка платёжной системы: возвраты и счётчики по ним."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f'http://127.0.0.1:{port}'
        env = {
            **os.environ,
            'STUB_PAYMENT_FAIL_RATE': '0.0',
            'STUB_PAYMENT_TIMEOUT_RATE': '0.0',
            'STUB_PAYMENT_HANG_SECONDS': '10',
        }
        self._proc = subprocess.Popen(
            [
                sys.executable, '-m', 'granian', '--interface', 'asgi',
                'stub_payment.app:app', '--host', '127.0.0.1', '--port', str(port),
            ],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def wait_ready(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if httpx.get(f'{self.url}/health', timeout=0.5).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.15)
        raise RuntimeError(f'Заглушка платёжки не поднялась на порту {self.port}')

    def set_mode(self, mode: str, hang_seconds: float | None = None) -> None:
        payload: dict = {'mode': mode}
        if hang_seconds is not None:
            payload['hang_seconds'] = hang_seconds
        httpx.post(f'{self.url}/_control', json=payload, timeout=5.0).raise_for_status()

    def stats(self) -> dict:
        return httpx.get(f'{self.url}/_stats', timeout=5.0).json()

    def refunded_amount(self) -> int:
        return self.stats()['refunded_amount']

    def refund_count(self) -> int:
        return self.stats()['refund_count']

    def stop(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


@pytest.fixture(scope='session')
def payments() -> AsyncIterator[StubPayment]:
    stub = StubPayment(free_port())
    stub.wait_ready()
    yield stub
    stub.stop()


@pytest.fixture(scope='session')
def suppliers() -> AsyncIterator[tuple[StubSupplier, StubSupplier]]:
    a = StubSupplier('a', 0, free_port())
    b = StubSupplier('b', 1, free_port())
    a.wait_ready()
    b.wait_ready()
    yield a, b
    a.stop()
    b.stop()


@pytest.fixture
def config(suppliers, payments) -> Config:
    a, b = suppliers
    cfg = Config()
    cfg.payment.url = payments.url
    cfg.payment.request_timeout = 1.0
    # Много короткоживущих движков: крупный пул упрётся в max_connections.
    cfg.postgres.pool_size = 2
    cfg.postgres.max_overflow = 8
    cfg.supplier.primary_url = a.url
    cfg.supplier.fallback_url = b.url
    # Тесты не должны ждать боевых бэкоффов.
    cfg.supplier.request_timeout = 1.0
    cfg.supplier.probe_after_seconds = 0
    # Лимит по умолчанию заведомо недостижим: он предмет отдельного теста,
    # а остальным мешал бы. Тест лимита опускает эти числа сам.
    cfg.supplier.rate_limit_per_minute = 6000
    cfg.supplier.rate_limit_burst = 200
    cfg.supplier.permit_wait_seconds = 2.0
    cfg.delivery.stuck_after_seconds = 1
    # Одна повторная попытка, затем возврат: цепочку «повтор -> возврат»
    # надо проверять, но не ждать боевых трёх заходов.
    cfg.delivery.max_attempts_before_refund = 2
    # Релей тоже не должен ждать боевых бэкоффов.
    cfg.outbox.backoff_base = 0.01
    cfg.outbox.backoff_max = 0.05
    cfg.outbox.max_attempts = 2
    # Ожидание места в лимите тоже не должно быть боевым.
    cfg.outbox.poll_interval_seconds = 0.05
    return cfg


@pytest.fixture(autouse=True)
def reset_stubs(suppliers, payments):
    a, b = suppliers
    a.set_mode('ok')
    b.set_mode('ok')
    payments.set_mode('ok')
    # Лимит — предмет отдельного теста; остальным он мешал бы.
    for stub in (a, b):
        stub.set_rate_limit(0, 10)
    # Пул ключей конечен, а прогон длинный. Опустевшая заглушка начинает
    # отвечать 409 вместо заданного режима, и тест проверяет уже не то,
    # что задумано.
    for stub in (a, b):
        if stub.available() < 10:
            stub.refill(50)
    yield


@pytest.fixture
async def clean_db(config) -> AsyncIterator[None]:
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        await session.execute(text(f'TRUNCATE {", ".join(TABLES)} CASCADE'))
        await session.commit()
    yield
    await maker.kw['bind'].dispose()


@pytest.fixture
async def container(config, clean_db):
    c = make_async_container(
        *setup_providers(), InlineNotifierProvider(), context={Config: config}
    )
    yield c
    await c.close()


def build_app(container):
    from dishka.integrations.litestar import setup_dishka
    from litestar import Litestar

    from gamer_shop.application.exceptions import ApplicationException
    from gamer_shop.presentation.api import router
    from gamer_shop.presentation.exception_handlers import application_exception_handler

    app = Litestar(
        route_handlers=[router],
        exception_handlers={ApplicationException: application_exception_handler},
        openapi_config=None,
    )
    setup_dishka(container=container, app=app)
    return app


@pytest.fixture
async def api(container) -> AsyncIterator[httpx.AsyncClient]:
    """Клиент поверх ASGI, без отдельного сервера."""
    transport = httpx.ASGITransport(app=build_app(container))
    async with httpx.AsyncClient(
        transport=transport, base_url='http://test/api/v1', timeout=60.0
    ) as client:
        yield client


@pytest.fixture
async def silent_container(config, clean_db):
    """Контейнер, в котором побудка релея не работает."""
    c = make_async_container(
        *setup_providers(), DeadNotifierProvider(), context={Config: config}
    )
    yield c
    await c.close()


@pytest.fixture
async def silent_api(silent_container) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(silent_container))
    async with httpx.AsyncClient(
        transport=transport, base_url='http://test/api/v1', timeout=60.0
    ) as client:
        yield client


@pytest.fixture
async def seeded(config, clean_db) -> None:
    """Каталог с известным остатком."""
    maker = new_session_maker(config.postgres)
    async with maker() as session:
        # Поставщики намеренно разные: заказ из нескольких товаров должен
        # расходиться по обоим.
        await session.execute(
            text(
                'INSERT INTO products '
                '  (sku, name, type, price, currency, supplier, is_active) VALUES '
                "('STEAM-TOPUP-500', 'Пополнение Steam 500 ₽', 'topup', 500, 'RUB', 'a', true), "
                "('KEY-CS2-PRIME', 'CS2 Prime Status ключ', 'key', 1290, 'RUB', 'b', true), "
                "('KEY-GTA5', 'GTA V ключ активации', 'key', 1990, 'RUB', 'a', true), "
                "('SUB-DISCORD-1M', 'Discord Nitro 1 месяц', 'subscription', 399, 'RUB', 'b', true)"
            )
        )
        await session.execute(
            text(
                "INSERT INTO product_stock (sku, available_count) VALUES "
                "('STEAM-TOPUP-500', 10), ('KEY-CS2-PRIME', 10), "
                "('KEY-GTA5', 10), ('SUB-DISCORD-1M', 1)"
            )
        )
        await session.commit()
    await maker.kw['bind'].dispose()
