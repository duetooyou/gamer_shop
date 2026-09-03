from typing import Protocol

from gamer_shop.application.dto import ProductDTO, StorefrontPageDTO


class ProductRepository(Protocol):
    async def get_active_by_sku(self, sku: str) -> ProductDTO | None: ...

    async def storefront_page(
        self, product_type: str | None, cursor: str | None, limit: int
    ) -> StorefrontPageDTO:
        """Витрина: карточки с остатком, keyset-пагинация."""
        ...


class StockRepository(Protocol):
    """Остаток по SKU. Все операции — атомарные UPDATE."""

    async def reserve(self, sku: str) -> bool:
        """Снять единицу в резерв. False — остатка нет."""
        ...

    async def release(self, sku: str) -> None:
        """Вернуть резерв в остаток."""
        ...

    async def commit_reserved(self, sku: str) -> None:
        """Списать резерв окончательно."""
        ...

    async def refill(self, sku: str, count: int) -> int:
        """Пополнить остаток."""
        ...

    async def available(self, sku: str) -> int: ...
