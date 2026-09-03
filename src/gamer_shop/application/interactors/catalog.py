from gamer_shop.application.dto import StorefrontPageDTO
from gamer_shop.application.interfaces import UoW
from gamer_shop.application.repositories import ProductRepository, StockRepository

MAX_PAGE_SIZE = 100


class StorefrontInteractor:
    """Витрина с остатками."""

    def __init__(self, products: ProductRepository) -> None:
        self._products = products

    async def execute(
        self, product_type: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> StorefrontPageDTO:
        return await self._products.storefront_page(
            product_type, cursor, min(max(limit, 1), MAX_PAGE_SIZE)
        )


class RefillStockInteractor:
    """Пополнение остатка."""

    def __init__(self, uow: UoW, stock: StockRepository) -> None:
        self._uow = uow
        self._stock = stock

    async def execute(self, sku: str, count: int) -> int:
        async with self._uow:
            return await self._stock.refill(sku, count)
