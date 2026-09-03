from collections.abc import AsyncIterable

import httpx
from dishka import Provider, Scope, provide

from gamer_shop.application.interfaces import DeliveryLogger, SupplierClient
from gamer_shop.infrastructure.config import Config
from gamer_shop.infrastructure.suppliers import SupplierHttpClient


class SupplierProvider(Provider):
    @provide(scope=Scope.APP)
    async def http_client(self) -> AsyncIterable[httpx.AsyncClient]:
        # Один клиент на процесс — иначе каждая выдача платит за новое соединение.
        async with httpx.AsyncClient() as client:
            yield client

    @provide(scope=Scope.APP)
    def supplier_client(
        self, config: Config, client: httpx.AsyncClient, logger: DeliveryLogger
    ) -> SupplierClient:
        return SupplierHttpClient(config.supplier, client, logger)
