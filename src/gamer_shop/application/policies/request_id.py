"""request_id для поставщика.

Функция позиции и поставщика, а не счётчик попыток: повтор обязан попасть
в тот же идентификатор, иначе поставщик выдаст второй код.
"""

from gamer_shop.application.enums import SupplierName


def build_request_id(order_item_id: str, supplier: SupplierName) -> str:
    """ord_00123-2 + поставщик a -> req_00123-2-a."""
    suffix = order_item_id.removeprefix('ord_')
    return f'req_{suffix}-{supplier.value}'


def build_item_id(order_id: str, position: int) -> str:
    """Позиция адресуется читаемо и выводится из заказа: ord_00123-1."""
    return f'{order_id}-{position}'
