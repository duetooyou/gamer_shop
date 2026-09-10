from dataclasses import dataclass

from gamer_shop.application.enums import SupplierName


@dataclass(frozen=True, slots=True)
class ProductDTO:
    sku: str
    name: str
    type: str
    price: int
    currency: str
    image: str | None
    is_active: bool
    # Поставщик товара: в одном заказе позиции расходятся по разным.
    supplier: SupplierName = SupplierName.A


@dataclass(frozen=True, slots=True)
class StorefrontItemDTO:
    """Карточка товара вместе с остатком."""

    sku: str
    name: str
    type: str
    price: int
    currency: str
    available_count: int


@dataclass(frozen=True, slots=True)
class StorefrontPageDTO:
    items: list[StorefrontItemDTO]
    # Курсор — последний sku страницы. OFFSET на тысячах SKU деградирует
    # линейно, курсор держит константное время.
    next_cursor: str | None
