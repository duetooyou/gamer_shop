from gamer_shop.application.dto import (
    CreateOrderDTO,
    NewOrderLine,
    OrderItemViewDTO,
    OrderViewDTO,
)
from gamer_shop.application.exceptions import OrderNotFoundException, ProductNotFoundException
from gamer_shop.application.interfaces import PaymentsLogger, UoW
from gamer_shop.application.repositories import (
    DeliveryRepository,
    OrderItemRepository,
    OrderRepository,
    ProductRepository,
)


MAX_LINES = 20
MAX_QUANTITY = 10


class CreateOrderInteractor:
    """Создание заказа из нескольких товаров.

    Количество разворачивается в отдельные позиции: у каждой свой поставщик,
    свой код и своя судьба, поэтому счётчик количества только мешал бы.
    """

    def __init__(
        self,
        uow: UoW,
        orders: OrderRepository,
        products: ProductRepository,
        logger: PaymentsLogger,
    ) -> None:
        self._uow = uow
        self._orders = orders
        self._products = products
        self._logger = logger

    async def execute(self, dto: CreateOrderDTO) -> OrderViewDTO:
        lines: list[NewOrderLine] = []
        for line in dto.lines[:MAX_LINES]:
            product = await self._products.get_active_by_sku(line.sku)
            if product is None:
                raise ProductNotFoundException(details={'sku': line.sku})
            # Цену и поставщика берём из каталога и фиксируем: с ценой потом
            # сверяется сумма вебхука, а поставщик определяет, кому идти.
            lines.extend(
                NewOrderLine(
                    sku=product.sku,
                    price=product.price,
                    currency=product.currency,
                    supplier=product.supplier,
                )
                for _ in range(min(max(line.quantity, 1), MAX_QUANTITY))
            )

        async with self._uow:
            order, items = await self._orders.create(lines)

        self._logger.info(
            'order_created',
            order_id=order.id,
            positions=len(items),
            amount=order.total_amount,
            currency=order.currency,
        )
        return OrderViewDTO(
            order=order, items=[OrderItemViewDTO(item=i) for i in items]
        )


class GetOrderInteractor:
    """Заказ вместе с позициями и выданными кодами."""

    def __init__(
        self,
        orders: OrderRepository,
        items: OrderItemRepository,
        deliveries: DeliveryRepository,
    ) -> None:
        self._orders = orders
        self._items = items
        self._deliveries = deliveries

    async def execute(self, order_id: str) -> OrderViewDTO:
        order = await self._orders.get_by_id(order_id)
        if order is None:
            raise OrderNotFoundException(details={'order_id': order_id})

        items = await self._items.list_by_order(order_id)
        codes = await self._deliveries.list_by_order(order_id)
        return OrderViewDTO(
            order=order,
            items=[
                OrderItemViewDTO(
                    item=item,
                    code=codes[item.id].code if item.id in codes else None,
                    delivered_by=codes[item.id].supplier if item.id in codes else None,
                )
                for item in items
            ],
        )
