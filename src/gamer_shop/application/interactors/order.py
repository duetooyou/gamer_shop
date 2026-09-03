from gamer_shop.application.dto import CreateOrderDTO, OrderDTO, OrderViewDTO
from gamer_shop.application.exceptions import OrderNotFoundException, ProductNotFoundException
from gamer_shop.application.interfaces import PaymentsLogger, UoW
from gamer_shop.application.repositories import (
    DeliveryRepository,
    OrderRepository,
    ProductRepository,
)


class CreateOrderInteractor:
    """Создание заказа по SKU."""

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

    async def execute(self, dto: CreateOrderDTO) -> OrderDTO:
        product = await self._products.get_active_by_sku(dto.sku)
        if product is None:
            raise ProductNotFoundException(details={'sku': dto.sku})

        async with self._uow:
            # Цену берём из каталога: с ней потом сверяется сумма вебхука.
            order = await self._orders.create(product.sku, product.price, product.currency)

        self._logger.info(
            'order_created',
            order_id=order.id,
            sku=order.sku,
            amount=order.price,
            currency=order.currency,
        )
        return order


class GetOrderInteractor:
    """Заказ по id вместе с выданным кодом."""

    def __init__(self, orders: OrderRepository, deliveries: DeliveryRepository) -> None:
        self._orders = orders
        self._deliveries = deliveries

    async def execute(self, order_id: str) -> OrderViewDTO:
        order = await self._orders.get_by_id(order_id)
        if order is None:
            raise OrderNotFoundException(details={'order_id': order_id})
        delivery = await self._deliveries.get_by_order(order_id)
        return OrderViewDTO(
            order=order,
            code=delivery.code if delivery else None,
            supplier=delivery.supplier if delivery else None,
        )
