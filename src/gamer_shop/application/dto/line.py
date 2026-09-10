from dataclasses import dataclass

from gamer_shop.application.enums import SupplierName


@dataclass(frozen=True, slots=True)
class NewOrderLine:
    """Позиция, готовая к записи: цена и поставщик уже зафиксированы."""

    sku: str
    price: int
    currency: str
    supplier: SupplierName
