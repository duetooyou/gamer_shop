"""request_id для поставщика.

Функция заказа и поставщика, а не счётчик попыток: повтор обязан попасть
в тот же идентификатор, иначе поставщик выдаст второй код.
"""

from gamer_shop.application.enums import SupplierName

_SUPPLIER_INDEX = {SupplierName.A: 1, SupplierName.B: 2}


def build_request_id(order_id: str, supplier: SupplierName) -> str:
    suffix = order_id.removeprefix('ord_')
    return f'req_{suffix}-{_SUPPLIER_INDEX[supplier]}'
