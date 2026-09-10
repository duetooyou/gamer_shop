from typing import Protocol

from gamer_shop.application.dto import SupplierOutcome
from gamer_shop.application.enums import SupplierName


class SupplierClient(Protocol):
    """Клиент к поставщику.

    Ответу поставщика веры нет: он может втихую отдать чужой код, подписать
    ответ не тем request_id или сказать «ошибка», выдав код на самом деле.
    Поэтому клиент возвращает не «успех/ошибка», а один из трёх исходов, и
    единственный, которому можно верить, — OK с ответом, подписанным тем же
    request_id, который отправляли.

    Повторов внутри нет: ими заведует релей аутбокса, иначе они прошли бы
    мимо учёта обращений и мимо лимита поставщика.
    """

    async def issue(
        self, supplier: SupplierName, request_id: str, sku: str, order_item_id: str
    ) -> SupplierOutcome: ...

    async def probe(
        self, supplier: SupplierName, request_id: str
    ) -> SupplierOutcome:
        """Что поставщик выдал по этому запросу на самом деле.

        Запрос состояния, а не новая выдача: только он отличает «не выдал»
        от «выдал и соврал».
        """
        ...
